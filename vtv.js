/**
 * VTV - Sistema de TV por streaming
 * Formato: ciclo de X dias (dia_1, dia_2, etc.)
 */

const CHANNELS_PATH = '/channels';
const HLS_PATH = '/movies_hls';

// Horário de início e fim da programação diária
const SCHEDULE_START_HOUR = 7;  // 07:00
const SCHEDULE_END_HOUR = 4;    // 04:00 (do próximo dia)

let currentChannel = null;
let channelData = null;
let allChannelsData = {}; // cache de dados de todos os canais
let hls = null;
let updateInterval = null;
let isPlaying = false;
let idleTimeout = null;
let serverTimeOffset = 0; // diferença entre servidor e navegador em ms
const IDLE_DELAY = 3000;
let isHoveringUI = false; // flag para indicar hover em elementos da UI

// Elementos DOM
const player = document.getElementById('player');
const overlay = document.getElementById('overlay');
const overlayText = document.getElementById('overlay-text');
const btnPlay = document.getElementById('btn-play');
const sidebar = document.getElementById('sidebar');
const channelList = document.getElementById('channel-list');
const btnToggle = document.getElementById('btn-toggle-sidebar');
const btnCloseSidebar = document.getElementById('btn-close-sidebar');
const btnSchedule = document.getElementById('btn-schedule');
const btnResize = document.getElementById('btn-resize');
const btnFullscreen = document.getElementById('btn-fullscreen');
const btnCloseModal = document.getElementById('btn-close-modal');
const scheduleModal = document.getElementById('schedule-modal');
const nowPlaying = document.getElementById('now-playing');
const nowPlayingText = document.getElementById('now-playing-text');
const volumeSlider = document.getElementById('volume');
const playerControls = document.getElementById('player-controls');

// EPG Elements
const epgTimeline = document.getElementById('epg-timeline');
const epgTimelineHeaderScroll = document.getElementById('epg-timeline-header-scroll');
const epgChannels = document.getElementById('epg-channels');
const epgPrograms = document.getElementById('epg-programs');
const epgProgramsScroll = document.getElementById('epg-programs-scroll');

// EPG scroll position persistence
let epgScrollPosition = { x: 0, y: 0 };
const EPG_PIXELS_PER_HOUR = 280;

// ============ Utilitários de tempo ============

function parseTime(timeStr) {
  const [h, m] = timeStr.split(':').map(Number);
  return h * 3600 + m * 60;
}

function formatTime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatTimeHHMM(seconds) {
  // Normaliza para 0-24h (segundos pode ser > 24h para horários após meia-noite)
  const normalizedSecs = seconds % (24 * 3600);
  const h = Math.floor(normalizedSecs / 3600);
  const m = Math.floor((normalizedSecs % 3600) / 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function formatCountdown(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function capitalize(str) {
  if (!str) return '';
  return str.split(' ').map((word, index) => {
    if (word.length > 1 || index === 0 || ['w','x','y','z'].indexOf(word) > -1) {
      return word.charAt(0).toUpperCase() + word.slice(1);
    }
    return word;
  }).join(' ').replace('tv','TV');
}

async function syncServerTime() {
  try {
    const res = await fetch('/api/time');
    if (!res.ok) throw new Error('Failed to fetch server time');
    const data = await res.json();

    // Calcula offset: timestamp do servidor - timestamp do navegador
    const serverTimestamp = data.timestamp * 1000; // converter para ms
    const browserTimestamp = Date.now();
    serverTimeOffset = serverTimestamp - browserTimestamp;

    return data;
  } catch (e) {
    console.warn('Could not sync server time, using browser time:', e);
    return null;
  }
}

function getServerNow() {
  // Retorna Date ajustado pelo offset do servidor
  return new Date(Date.now() + serverTimeOffset);
}

function getSecondsOfDay(date) {
  return date.getHours() * 3600 + date.getMinutes() * 60 + date.getSeconds();
}

// ============ Lógica de ciclo de dias ============

/**
 * Retorna o número máximo de dias no ciclo do canal
 */
function getCycleDays(chData) {
  let maxDay = 0;
  for (const key of Object.keys(chData)) {
    if (key.startsWith('dia_')) {
      const num = parseInt(key.split('_')[1], 10);
      if (!isNaN(num) && num > maxDay) {
        maxDay = num;
      }
    }
  }
  return maxDay;
}

/**
 * Calcula qual dia do ciclo estamos baseado em cycle_start
 * Considera que o "dia de programação" vai de 7h às 3h do próximo dia
 */
function getCurrentCycleDay(chData, now) {
  const cycleStart = chData.cycle_start;
  const totalDays = getCycleDays(chData);

  if (!cycleStart || totalDays === 0) {
    return null;
  }

  // Ajusta a hora para considerar que o dia vai de 7h às 3h
  // Se são antes das 7h (ex: 2h da manhã), ainda conta como dia anterior
  let adjustedDate = new Date(now);
  if (now.getHours() < SCHEDULE_START_HOUR) {
    // Antes das 7h, ainda é o dia de programação anterior
    adjustedDate.setDate(adjustedDate.getDate() - 1);
  }

  // Calcula dias desde o início do ciclo
  const startDate = new Date(cycleStart + 'T00:00:00');
  const diffTime = adjustedDate.getTime() - startDate.getTime();
  const diffDays = Math.floor(diffTime / (24 * 60 * 60 * 1000));

  // Calcula dia do ciclo (1-indexed, com loop)
  let cycleDay = (diffDays % totalDays) + 1;
  if (cycleDay <= 0) {
    cycleDay += totalDays;
  }

  return cycleDay;
}

/**
 * Retorna a programação do dia atual do ciclo
 */
function getTodayPrograms(chData, now) {
  const cycleDay = getCurrentCycleDay(chData, now);
  if (!cycleDay) return [];

  const dayKey = `dia_${cycleDay}`;
  return chData[dayKey] || [];
}

/**
 * Expande a programação do dia, calculando horários de início
 * Retorna lista de { id, start, duration, fullDuration }
 *
 * O "dia de programação" vai de 07:00 às 03:00 (próximo dia calendário)
 * Horários entre 00:00-03:00 são tratados como 24:00-27:00 para ordenação
 */
function expandSchedule(programs) {
  const result = [];
  let currentTime = SCHEDULE_START_HOUR * 3600; // começa às 07:00

  for (let i = 0; i < programs.length; i++) {
    const prog = programs[i];

    // Se tem start definido, usa ele
    if (prog.start) {
      let startSecs = parseTime(prog.start);
      // Se o horário é entre 00:00 e 03:00, é na verdade após meia-noite
      // do dia de programação (24:00-27:00)
      if (startSecs < SCHEDULE_START_HOUR * 3600) {
        startSecs += 24 * 3600;
      }
      currentTime = startSecs;
    } else if (i === 0) {
      // Primeiro programa sem start: começa às 07:00
      currentTime = SCHEDULE_START_HOUR * 3600;
    }
    // Senão: começa após o anterior (currentTime já está correto)

    result.push({
      id: prog.id,
      start: currentTime, // pode ser > 24h para programas após meia-noite
      duration: prog.duration,
      fullDuration: prog.duration
    });

    currentTime += prog.duration;
  }

  return result;
}

/**
 * Encontra o programa atual baseado nos segundos do dia
 * nowSeconds pode ser ajustado para +24h se estiver entre 00:00-07:00
 */
function findCurrentProgram(programs, nowSeconds) {
  // Se estamos entre 00:00 e 07:00, ajusta para comparar com programas após meia-noite
  let adjustedNowSeconds = nowSeconds;
  if (nowSeconds < SCHEDULE_START_HOUR * 3600) {
    adjustedNowSeconds = nowSeconds + 24 * 3600;
  }

  for (let i = 0; i < programs.length; i++) {
    const prog = programs[i];
    const progStart = prog.start;
    const progEnd = prog.start + prog.duration;

    if (adjustedNowSeconds >= progStart && adjustedNowSeconds < progEnd) {
      const offset = adjustedNowSeconds - progStart;
      return { program: prog, offset, index: i };
    }
  }
  return null;
}

/**
 * Encontra o próximo programa (hoje ou amanhã no ciclo)
 */
function findNextProgram(chData, now) {
  const nowSeconds = getSecondsOfDay(now);
  const todayPrograms = expandSchedule(getTodayPrograms(chData, now));

  // Ajusta nowSeconds para comparação (se entre 00:00-07:00, adiciona 24h)
  let adjustedNowSeconds = nowSeconds;
  if (nowSeconds < SCHEDULE_START_HOUR * 3600) {
    adjustedNowSeconds = nowSeconds + 24 * 3600;
  }

  // Procura no mesmo dia de programação
  for (const prog of todayPrograms) {
    if (prog.start > adjustedNowSeconds) {
      // Calcula segundos até o programa
      let secondsUntil = prog.start - adjustedNowSeconds;
      return {
        program: prog,
        secondsUntil: secondsUntil,
        isToday: true
      };
    }
  }

  // Procura no próximo dia do ciclo
  // Se estamos antes das 07:00, o "próximo dia de programação" é o mesmo dia calendário às 07:00
  // Senão, é o próximo dia calendário às 07:00
  const tomorrow = new Date(now);
  if (now.getHours() >= SCHEDULE_START_HOUR) {
    tomorrow.setDate(tomorrow.getDate() + 1);
  }
  tomorrow.setHours(SCHEDULE_START_HOUR, 0, 0, 0);

  const tomorrowPrograms = expandSchedule(getTodayPrograms(chData, tomorrow));

  if (tomorrowPrograms.length > 0) {
    const firstProg = tomorrowPrograms[0];
    // Calcula segundos até o primeiro programa de amanhã
    // firstProg.start é relativo ao início do dia de programação (07:00)
    const secondsUntil7am = (SCHEDULE_START_HOUR * 3600 - nowSeconds + 24 * 3600) % (24 * 3600);
    const secondsAfter7am = firstProg.start - SCHEDULE_START_HOUR * 3600;
    return {
      program: firstProg,
      secondsUntil: secondsUntil7am + secondsAfter7am,
      isToday: false
    };
  }

  return null;
}

// ============ Status do canal ============

function getChannelStatus(chData, now) {
  if (!chData) return { status: 'offline', label: 'Fora do ar' };

  const totalDays = getCycleDays(chData);
  if (totalDays === 0) {
    return { status: 'offline', label: 'Fora do ar' };
  }

  const nowSeconds = getSecondsOfDay(now);
  const programs = expandSchedule(getTodayPrograms(chData, now));
  const current = findCurrentProgram(programs, nowSeconds);

  if (current) {
    return { status: 'live', label: 'No ar' };
  }

  const next = findNextProgram(chData, now);
  if (next) {
    return { status: 'soon', label: 'Em breve' };
  }

  return { status: 'offline', label: 'Fora do ar' };
}

/**
 * Retorna lista dos próximos N vídeos
 */
function getUpcomingVideos(chData, now, count = 3) {
  if (!chData) return [];

  const nowSeconds = getSecondsOfDay(now);
  const todayPrograms = expandSchedule(getTodayPrograms(chData, now));
  const upcoming = [];

  // Encontra programa atual para saber o índice
  const current = findCurrentProgram(todayPrograms, nowSeconds);
  let startIndex = 0;

  if (current) {
    startIndex = current.index + 1; // começa do próximo
  } else {
    // Não há programa atual, procura o primeiro que ainda não começou
    // Ajusta nowSeconds para comparação (se entre 00:00-07:00, adiciona 24h)
    let adjustedNowSeconds = nowSeconds;
    if (nowSeconds < SCHEDULE_START_HOUR * 3600) {
      adjustedNowSeconds = nowSeconds + 24 * 3600;
    }
    for (let i = 0; i < todayPrograms.length; i++) {
      if (todayPrograms[i].start > adjustedNowSeconds) {
        startIndex = i;
        break;
      }
      startIndex = todayPrograms.length;
    }
  }

  // Adiciona programas de hoje
  for (let i = startIndex; i < todayPrograms.length && upcoming.length < count; i++) {
    upcoming.push({
      ...todayPrograms[i],
      day: 'hoje'
    });
  }

  // Se precisar de mais, pega do próximo dia do ciclo
  // Se estamos antes das 07:00, o "próximo dia de programação" é o mesmo dia calendário às 07:00
  if (upcoming.length < count) {
    const tomorrow = new Date(now);
    if (now.getHours() >= SCHEDULE_START_HOUR) {
      tomorrow.setDate(tomorrow.getDate() + 1);
    }
    tomorrow.setHours(SCHEDULE_START_HOUR, 0, 0, 0);
    const tomorrowPrograms = expandSchedule(getTodayPrograms(chData, tomorrow));

    for (let i = 0; i < tomorrowPrograms.length && upcoming.length < count; i++) {
      upcoming.push({
        ...tomorrowPrograms[i],
        day: 'amanhã'
      });
    }
  }

  return upcoming;
}

// ============ Player HLS ============

function destroyPlayer() {
  if (hls) {
    hls.destroy();
    hls = null;
  }
  player.src = '';
  player.load();
}

function loadVideo(videoId, startOffset) {
  destroyPlayer();

  const m3u8Url = `${HLS_PATH}/${videoId}/stream.m3u8?v=`+Date.now();

  if (Hls.isSupported()) {
    hls = new Hls({
      startPosition: startOffset,
      debug: false
    });

    hls.loadSource(m3u8Url);
    hls.attachMedia(player);

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      player.currentTime = startOffset;
      if (isPlaying) {
        player.play().catch(e => console.log('Autoplay blocked:', e));
      }
    });

    hls.on(Hls.Events.ERROR, (event, data) => {
      console.error('HLS Error:', data);
      if (data.fatal) {
        console.error('Fatal error, attempting recovery...');
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          hls.startLoad();
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError();
        }
      }
    });
  } else if (player.canPlayType('application/vnd.apple.mpegurl')) {
    player.src = m3u8Url;
    player.addEventListener('loadedmetadata', () => {
      player.currentTime = startOffset;
      if (isPlaying) {
        player.play().catch(e => console.log('Autoplay blocked:', e));
      }
    }, { once: true });
  } else {
    console.error('HLS not supported');
  }
}

// ============ Atualização em tempo real ============

function updateNowPlaying() {
  if (!channelData) return;

  const now = getServerNow();
  const nowSeconds = getSecondsOfDay(now);
  const programs = expandSchedule(getTodayPrograms(channelData, now));
  const current = findCurrentProgram(programs, nowSeconds);

  if (current) {
    const { program, offset } = current;
    const remaining = program.duration - offset;
    const title = capitalize(program.id.replace(/_/g, ' '));
    nowPlayingText.textContent = `${title} • ${formatTime(remaining)} restantes`;
    nowPlaying.classList.remove('hidden');

    if (remaining <= 1) {
      setTimeout(() => syncToSchedule(), 2000);
    }
  } else {
    // Verifica se há próximo programa e atualiza o timer
    const nextProg = findNextProgram(channelData, now);
    if (nextProg) {
      nowPlayingText.textContent = `Próximo em ${formatCountdown(nextProg.secondsUntil)}`;

      // Atualiza o countdown no h1 do overlay se visível
      if (!overlay.classList.contains('hidden')) {
        overlay.querySelector('h1').textContent = formatCountdown(nextProg.secondsUntil);
      }

      // Quando chegar a hora, sincroniza com a programação
      if (nextProg.secondsUntil <= 1) {
        setTimeout(() => syncToSchedule(), 1000);
      }
    } else {
      nowPlayingText.textContent = 'Fora do ar';
    }
    nowPlaying.classList.remove('hidden');
  }
}

function renderUpcomingTable(upcoming) {
  if (!upcoming || upcoming.length === 0) return '';

  let html = '<div class="upcoming-table"><div class="upcoming-title">Próximos 3 Filmes</div>';
  for (const prog of upcoming) {
    const title = capitalize(prog.id.replace(/_/g, ' '));
    const startTime = formatTimeHHMM(prog.start);
    const dayLabel = prog.day === 'amanhã' ? '<span class="day-tag">amanhã</span>' : '';

    html += `
      <div class="upcoming-item">
        <span class="upcoming-time">${startTime}</span>
        <span class="upcoming-name">${title}</span>
        ${dayLabel}
      </div>
    `;
  }
  html += '<a href="#" class="upcoming-link" id="btn-upcoming-schedule">Ver programação completa</a>';
  html += '</div>';
  return html;
}

function updateOverlayUpcoming() {
  if (!channelData || overlay.classList.contains('hidden')) return;

  const now = getServerNow();
  const nowSeconds = getSecondsOfDay(now);
  const programs = expandSchedule(getTodayPrograms(channelData, now));
  const current = findCurrentProgram(programs, nowSeconds);

  // Só atualiza se não estiver transmitindo (timer ativo)
  if (current) return;

  const nextProg = findNextProgram(channelData, now);
  
  if (nextProg) {
    // Atualiza countdown
    overlay.querySelector('h1').textContent = formatCountdown(nextProg.secondsUntil);

    // Atualiza tabela de próximos
    const existingTable = overlay.querySelector('.upcoming-table');
    const upcoming = getUpcomingVideos(channelData, now, 3);

    if (upcoming.length > 0) {
      const newHtml = renderUpcomingTable(upcoming);
      if (existingTable) {
        existingTable.outerHTML = newHtml;
      } else {
        overlayText.insertAdjacentHTML('afterend', newHtml);
      }
    }
  }
}

function syncToSchedule() {
  if (!channelData) return;

  const now = getServerNow();
  const nowSeconds = getSecondsOfDay(now);
  const programs = expandSchedule(getTodayPrograms(channelData, now));
  const current = findCurrentProgram(programs, nowSeconds);

  // Remove elementos dinâmicos do overlay
  const existingTable = overlay.querySelector('.upcoming-table');
  if (existingTable) existingTable.remove();
  const existingCards = overlay.querySelector('.home-channels');
  if (existingCards) existingCards.remove();

  if (current) {
    const { program, offset } = current;
    loadVideo(program.id, offset);

    // Mostra overlay com botão play se ainda não clicou
    if (!isPlaying) {
      overlay.querySelector('h1').textContent = capitalize(currentChannel);
      overlayText.textContent = capitalize(program.id.replace(/_/g, ' '));
      btnPlay.classList.remove('hidden');
      overlay.classList.remove('hidden');
    } else {
      overlay.classList.add('hidden');
    }
  } else {
    destroyPlayer();

    // Verifica se há próximo programa
    const nextProg = findNextProgram(channelData, now);
    if (nextProg) {
      overlay.querySelector('h1').textContent = formatCountdown(nextProg.secondsUntil);
      overlayText.textContent = capitalize(currentChannel) + ' volta já :)';

      // Mostra tabela de próximos vídeos
      const upcoming = getUpcomingVideos(channelData, now, 3);
      if (upcoming.length > 0) {
        overlayText.insertAdjacentHTML('afterend', renderUpcomingTable(upcoming));
      }
    } else {
      overlay.querySelector('h1').textContent = capitalize(currentChannel);
      overlayText.textContent = 'Fora do ar no momento';
    }

    // Mostra lista de canais
    const cardsHtml = renderHomeChannels();
    if (cardsHtml) {
      const lastElement = overlay.querySelector('.upcoming-table') || overlayText;
      lastElement.insertAdjacentHTML('afterend', cardsHtml);
    }

    btnPlay.classList.add('hidden');
    overlay.classList.remove('hidden');
  }
}

// ============ Programação EPG (modal) ============

function getEPGPrograms(chData, startHour, hoursCount = 48) {
  // Retorna programas das próximas 48 horas a partir de startHour
  const now = getServerNow();
  const programs = [];

  // Começa a partir da hora atual (arredondada para baixo)
  const currentDate = new Date(now);
  currentDate.setMinutes(0, 0, 0);
  currentDate.setHours(startHour);

  // Se estamos antes das 07:00, o dia de programação atual começou ontem
  // Ajusta a data base para isso
  let baseDateOffset = 0;
  if (startHour < SCHEDULE_START_HOUR) {
    baseDateOffset = -1;
  }

  // Coleta programas de hoje e dos próximos 2 dias no ciclo
  for (let dayOffset = 0; dayOffset <= 2; dayOffset++) {
    const targetDate = new Date(currentDate);
    targetDate.setDate(targetDate.getDate() + dayOffset + baseDateOffset);
    targetDate.setHours(SCHEDULE_START_HOUR, 0, 0, 0);

    const dayPrograms = expandSchedule(getTodayPrograms(chData, targetDate));

    for (const prog of dayPrograms) {
      programs.push({
        ...prog,
        dayOffset: dayOffset,
        absoluteStart: dayOffset * 24 * 3600 + prog.start
      });
    }
  }

  return programs;
}

function renderEPG() {
  const channelNames = Object.keys(allChannelsData);

  if (channelNames.length === 0) {
    epgChannels.innerHTML = '<div style="padding: 2rem; color: #666;">Nenhum canal disponível</div>';
    return;
  }

  const now = getServerNow();
  const currentHour = now.getHours();
  const currentMinute = now.getMinutes();
  const currentSeconds = now.getSeconds();
  const nowSeconds = getSecondsOfDay(now);

  // Coleta todos os programas de todos os canais para descobrir quais horas têm programação
  const hoursWithContent = new Set();
  // Se estamos antes das 07:00, ajusta startOfTimeline para +24h
  // porque os programas do dia de programação atual têm start relativo ao dia anterior
  let startOfTimeline = currentHour * 3600;
  if (currentHour < SCHEDULE_START_HOUR) {
    startOfTimeline += 24 * 3600;
  }
  const timelineEndSec = startOfTimeline + 48 * 3600;

  for (const chName of channelNames) {
    const chData = allChannelsData[chName];
    const programs = getEPGPrograms(chData, currentHour, 48);

    for (const prog of programs) {
      let progStartSec = prog.absoluteStart;
      let progEndSec = progStartSec + prog.duration;

      // Ignora programas fora da janela de 24h
      if (progEndSec <= startOfTimeline || progStartSec >= timelineEndSec) {
        continue;
      }

      // Clipa ao início/fim da timeline
      if (progStartSec < startOfTimeline) progStartSec = startOfTimeline;
      if (progEndSec > timelineEndSec) progEndSec = timelineEndSec;

      // Marca todas as horas que este programa cobre
      const startHourOffset = Math.floor((progStartSec - startOfTimeline) / 3600);
      const endHourOffset = Math.ceil((progEndSec - startOfTimeline) / 3600);
      for (let h = startHourOffset; h < endHourOffset && h < 48; h++) {
        hoursWithContent.add(h);
      }
    }
  }

  // Converte para array ordenado de offsets de hora que têm conteúdo
  const activeHours = Array.from(hoursWithContent).sort((a, b) => a - b);

  // Se não há programação, mostra mensagem
  if (activeHours.length === 0) {
    epgTimeline.innerHTML = '<div class="epg-time-slot">Sem programação</div>';
    epgChannels.innerHTML = '';
    epgPrograms.innerHTML = '';
    return;
  }

  // Cria mapeamento de offset de hora para posição na timeline
  const hourToPosition = {};
  activeHours.forEach((hourOffset, index) => {
    hourToPosition[hourOffset] = index;
  });

  // Renderiza timeline (apenas horas com conteúdo)
  let timelineHtml = '';
  for (const hourOffset of activeHours) {
    const hour = (currentHour + hourOffset) % 24;
    const label = `${String(hour).padStart(2, '0')}:00`;
    timelineHtml += `<div class="epg-time-slot">${label}</div>`;
  }
  epgTimeline.innerHTML = timelineHtml;

  // Renderiza canais (coluna fixa)
  let channelsHtml = '';
  for (const chName of channelNames) {
    const chData = allChannelsData[chName];
    const { status, label } = getChannelStatus(chData, now);
    const isActive = chName === currentChannel;

    channelsHtml += `
      <div class="epg-channel-row ${isActive ? 'active' : ''}" data-channel="${chName}">
        <div class="epg-channel-name">${capitalize(chName)}</div>
        <div class="epg-channel-status status-${status}">
          <span class="status-dot"></span>
          <span class="status-label">${label}</span>
        </div>
      </div>
    `;
  }
  epgChannels.innerHTML = channelsHtml;

  // Renderiza programação (grid horizontal)
  let programsHtml = '';

  // Offset em minutos da hora atual (para posicionar programas corretamente)
  const minuteOffset = currentMinute + (currentSeconds / 60);

  // Largura total da timeline (apenas horas com conteúdo)
  const totalWidth = activeHours.length * EPG_PIXELS_PER_HOUR;

  for (const chName of channelNames) {
    const chData = allChannelsData[chName];
    const programs = getEPGPrograms(chData, currentHour, 48);

    programsHtml += '<div class="epg-program-row">';

    // Calcula posição de cada programa
    // Cada hora ativa tem 200px de largura

    let lastEndPx = 0;

    for (const prog of programs) {
      // Calcula posição do programa relativa ao início da timeline
      let progStartSec = prog.absoluteStart;
      let progEndSec = progStartSec + prog.duration;

      // Ignora programas fora da janela de 24h
      if (progEndSec <= startOfTimeline || progStartSec >= timelineEndSec) {
        continue;
      }

      // Clipa ao início/fim da timeline
      if (progStartSec < startOfTimeline) {
        progStartSec = startOfTimeline;
      }
      if (progEndSec > timelineEndSec) {
        progEndSec = timelineEndSec;
      }

      // Calcula offset de hora do início e fim do programa
      const progStartHourOffset = Math.floor((progStartSec - startOfTimeline) / 3600);
      const progEndHourOffset = Math.ceil((progEndSec - startOfTimeline) / 3600);

      // Verifica se este programa está em alguma hora ativa
      let progInActiveHours = false;
      for (let h = progStartHourOffset; h < progEndHourOffset; h++) {
        if (hoursWithContent.has(h)) {
          progInActiveHours = true;
          break;
        }
      }
      if (!progInActiveHours) continue;

      // Calcula posição na timeline compactada
      // Encontra a primeira hora ativa que este programa cobre
      let startPx = 0;
      const startHourOffset = Math.floor((progStartSec - startOfTimeline) / 3600);
      if (hourToPosition[startHourOffset] !== undefined) {
        const fractionInHour = ((progStartSec - startOfTimeline) % 3600) / 3600;
        startPx = hourToPosition[startHourOffset] * EPG_PIXELS_PER_HOUR + fractionInHour * EPG_PIXELS_PER_HOUR;
      } else {
        // Programa começa numa hora sem conteúdo, encontra próxima hora ativa
        for (let h = startHourOffset; h < 48; h++) {
          if (hourToPosition[h] !== undefined) {
            startPx = hourToPosition[h] * EPG_PIXELS_PER_HOUR;
            break;
          }
        }
      }

      // Calcula largura baseada nas horas ativas que o programa cobre
      let widthPx = 0;
      for (let h = progStartHourOffset; h < progEndHourOffset && h < 48; h++) {
        if (hourToPosition[h] !== undefined) {
          // Calcula quanto deste programa está nesta hora
          const hourStart = startOfTimeline + h * 3600;
          const hourEnd = hourStart + 3600;
          const overlapStart = Math.max(progStartSec, hourStart);
          const overlapEnd = Math.min(progEndSec, hourEnd);
          const overlapDuration = overlapEnd - overlapStart;
          widthPx += (overlapDuration / 3600) * EPG_PIXELS_PER_HOUR;
        }
      }

      if (widthPx <= 0) continue;

      // Preenche espaço vazio se houver
      if (startPx > lastEndPx + 1) {
        const gapWidth = startPx - lastEndPx;
        programsHtml += `<div class="epg-program-empty" style="width: ${gapWidth}px; min-width: ${gapWidth}px;"></div>`;
      }

      // Verifica se é o programa atual (ajusta nowSeconds se antes das 07:00)
      let adjustedNow = nowSeconds;
      if (nowSeconds < SCHEDULE_START_HOUR * 3600) {
        adjustedNow = nowSeconds + 24 * 3600;
      }
      const isCurrent = prog.dayOffset === 0 &&
        prog.start <= adjustedNow &&
        (prog.start + prog.duration) > adjustedNow;

      const title = capitalize(prog.id.replace(/_/g, ' '));
      const startTime = formatTimeHHMM(prog.start);
      const endTime = formatTimeHHMM((prog.start + prog.duration) % (24 * 3600));

      programsHtml += `
        <div class="epg-program ${isCurrent ? 'current' : ''}"
             style="width: ${widthPx}px; min-width: ${widthPx}px;"
             data-channel="${chName}"
             title="${title} (${startTime} - ${endTime})">
          <div class="epg-program-title">${title}</div>
          <div class="epg-program-time">${startTime} - ${endTime}</div>
        </div>
      `;

      lastEndPx = startPx + widthPx;
    }

    // Preenche o resto se necessário
    if (lastEndPx < totalWidth) {
      const remainingWidth = totalWidth - lastEndPx;
      programsHtml += `<div class="epg-program-empty" style="width: ${remainingWidth}px; min-width: ${remainingWidth}px;"></div>`;
    }

    programsHtml += '</div>';
  }

  // Calcula posição do indicador NOW na timeline compactada
  let nowIndicatorPx = -1;
  const currentHourOffset = 0; // Hora atual é sempre offset 0
  if (hourToPosition[currentHourOffset] !== undefined) {
    nowIndicatorPx = hourToPosition[currentHourOffset] * EPG_PIXELS_PER_HOUR + (minuteOffset / 60) * EPG_PIXELS_PER_HOUR;
  }

  epgPrograms.innerHTML = programsHtml;

  // Adiciona indicador NOW após renderizar (apenas se a hora atual está visível)
  if (nowIndicatorPx >= 0) {
    const nowIndicator = document.createElement('div');
    nowIndicator.className = 'epg-now-indicator';
    nowIndicator.style.left = `${nowIndicatorPx}px`;
    epgPrograms.appendChild(nowIndicator);
  }

  // Restaura posição do scroll
  epgProgramsScroll.scrollLeft = epgScrollPosition.x;
  epgProgramsScroll.scrollTop = epgScrollPosition.y;
  epgChannels.scrollTop = epgScrollPosition.y;
  epgTimelineHeaderScroll.scrollLeft = epgScrollPosition.x;
}

function setupEPGScrollSync() {
  // Sincroniza scroll horizontal entre timeline e programação
  epgProgramsScroll.addEventListener('scroll', () => {
    epgTimelineHeaderScroll.scrollLeft = epgProgramsScroll.scrollLeft;
    epgChannels.scrollTop = epgProgramsScroll.scrollTop;

    // Salva posição
    epgScrollPosition.x = epgProgramsScroll.scrollLeft;
    epgScrollPosition.y = epgProgramsScroll.scrollTop;
  });

  // Sincroniza scroll vertical da coluna de canais
  epgChannels.addEventListener('scroll', () => {
    epgProgramsScroll.scrollTop = epgChannels.scrollTop;
    epgScrollPosition.y = epgChannels.scrollTop;
  });

  // Scroll horizontal com roda do mouse no EPG
  epgProgramsScroll.addEventListener('wheel', (e) => {
    // Se não estiver segurando Shift, converte scroll vertical em horizontal
    if (!e.shiftKey) {
      e.preventDefault();
      epgProgramsScroll.scrollLeft += e.deltaY;
    }
  }, { passive: false });

  // Também na timeline
  epgTimelineHeaderScroll.addEventListener('wheel', (e) => {
    if (!e.shiftKey) {
      e.preventDefault();
      epgProgramsScroll.scrollLeft += e.deltaY;
    }
  }, { passive: false });
}

function openEPG() {
  renderEPG();
  scheduleModal.classList.remove('hidden');

  // Scroll para mostrar hora atual (um pouco antes)
  if (epgScrollPosition.x === 0 && epgScrollPosition.y === 0) {
    // Primeira vez abrindo, não faz scroll automático
    // já que a timeline começa na hora atual
  }
}

// ============ Carregamento de canais ============

async function loadChannelList() {
  try {
    const knownChannels = ['imaginarium','superhero','animetv','rewindtv','neverland','paradox','afterdark'];

    const channels = [];
    for (const name of knownChannels) {
      try {
        const res = await fetch(`${CHANNELS_PATH}/${name}.json?v=` + Date.now());
        if (res.ok) {
          channels.push(name);
          allChannelsData[name] = await res.json();
        }
      } catch (e) {
        // Ignora
      }
    }

    if (channels.length === 0) {
      channels.push('anos90');
    }

    renderChannelList(channels);
  } catch (e) {
    console.error('Error loading channels:', e);
  }
}

function renderChannelList(channels) {
  const now = getServerNow();

  channelList.innerHTML = channels.map(name => {
    const chData = allChannelsData[name];
    const { status, label } = getChannelStatus(chData, now);

    return `
      <li>
        <a href="?channel=${name}" data-channel="${name}">
          <span class="channel-name">${capitalize(name)}</span>
          <span class="channel-status status-${status}">
            <span class="status-dot"></span>
            <span class="status-label">${label}</span>
          </span>
        </a>
      </li>
    `;
  }).join('');

  updateActiveChannel();
}

function updateChannelStatuses() {
  const now = getServerNow();
  document.querySelectorAll('#channel-list a').forEach(a => {
    const name = a.dataset.channel;
    const chData = allChannelsData[name];
    const { status, label } = getChannelStatus(chData, now);

    const statusEl = a.querySelector('.channel-status');
    if (statusEl) {
      statusEl.className = `channel-status status-${status}`;
      statusEl.querySelector('.status-label').textContent = label;
    }
  });
}

function updateActiveChannel() {
  document.querySelectorAll('#channel-list a').forEach(a => {
    a.classList.toggle('active', a.dataset.channel === currentChannel);
  });
}

async function loadChannel(name) {
  if (currentChannel === name && channelData) {
    return;
  }

  // Fecha menu ao carregar canal
  closeSidebar();

  try {
    const res = await fetch(`${CHANNELS_PATH}/${name}.json?v=` + Date.now());
    if (!res.ok) throw new Error('Channel not found');

    channelData = await res.json();
    currentChannel = name;

    updateActiveChannel();
    syncToSchedule();

    if (updateInterval) clearInterval(updateInterval);
    updateInterval = setInterval(() => {
      updateNowPlaying();
      updateChannelStatuses();
      updateOverlayUpcoming();
    }, 1000);
    updateNowPlaying();

  } catch (e) {
    console.error('Error loading channel:', e);
    overlay.querySelector('h1').textContent = 'Erro';
    overlayText.textContent = 'Canal não encontrado';
    btnPlay.classList.add('hidden');
    overlay.classList.remove('hidden');
  }
}

// ============ Sidebar ============

function openSidebar() {
  sidebar.classList.remove('hidden');
  btnToggle.classList.add('hidden');
}

function closeSidebar() {
  sidebar.classList.add('hidden');
  btnToggle.classList.remove('hidden');
}

// ============ Fullscreen ============

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(e => {
      console.log('Fullscreen error:', e);
    });
  } else {
    document.exitFullscreen();
  }
}

// ============ Resize (object-fit) ============

function loadResizePreference() {
  const pref = localStorage.getItem('vtv-video-object-fit');
  // Padrão é contain (com classe), cover remove classe
  if (pref === 'cover') {
    player.classList.remove('contain');
    btnResize.textContent = '⊡';
    btnResize.title = 'Ajustar ao vídeo';
  } else {
    player.classList.add('contain');
    btnResize.textContent = '⊞';
    btnResize.title = 'Preencher tela';
  }
}

function loadVolumePreference() {
  const savedVolume = localStorage.getItem('vtv-volume');
  if (savedVolume !== null) {
    volumeSlider.value = savedVolume;
    player.volume = savedVolume / 100;
  }
}

function toggleResize() {
  const isContain = player.classList.toggle('contain');
  if (isContain) {
    localStorage.setItem('vtv-video-object-fit', 'contain');
    btnResize.textContent = '⊞';
    btnResize.title = 'Preencher tela';
  } else {
    localStorage.setItem('vtv-video-object-fit', 'cover');
    btnResize.textContent = '⊡';
    btnResize.title = 'Ajustar ao vídeo';
  }
}

// ============ Idle (esconde UI após inatividade) ============

function setIdle() {
  if (isPlaying) {
    document.body.classList.add('idle');
    playerControls.classList.remove('visible');
  }
}

function setActive() {
  document.body.classList.remove('idle');
  if (isPlaying) {
    playerControls.classList.add('visible');
  }
  clearTimeout(idleTimeout);
  // Só reinicia o timer se não estiver em hover sobre elementos da UI
  if (!isHoveringUI) {
    idleTimeout = setTimeout(setIdle, IDLE_DELAY);
  }
}

// ============ Play ============

function startPlayback() {
  isPlaying = true;
  player.muted = false;
  player.volume = volumeSlider.value / 100;
  player.play().catch(e => console.log('Play error:', e));
  overlay.classList.add('hidden');
  closeSidebar();
  setActive(); // Inicia timer de idle
}

// ============ Event Handlers ============

function renderHomeChannels() {
  const channelNames = Object.keys(allChannelsData);
  if (channelNames.length === 0) return '';

  const now = getServerNow();

  let html = '<div class="home-channels">';
  for (const chName of channelNames) {
    const chData = allChannelsData[chName];
    const { status, label } = getChannelStatus(chData, now);

    // Pega filme atual ou próximo
    const nowSeconds = getSecondsOfDay(now);
    const programs = expandSchedule(getTodayPrograms(chData, now));
    const current = findCurrentProgram(programs, nowSeconds);

    let filmInfo = '';
    if (current) {
      const title = capitalize(current.program.id.replace(/_/g, ' '));
      filmInfo = `<span class="home-film-label">Agora:</span> ${title}`;
    } else {
      const next = findNextProgram(chData, now);
      if (next) {
        const title = capitalize(next.program.id.replace(/_/g, ' '));
        const time = formatTimeHHMM(next.program.start);
        filmInfo = `<span class="home-film-label">Próximo ${time}:</span> ${title}`;
      } else {
        filmInfo = 'Sem programação';
      }
    }

    html += `
      <button class="home-channel-card" data-channel="${chName}">
        <div class="home-channel-top">
          <span class="home-channel-name">${capitalize(chName)}</span>
          <span class="home-channel-status status-${status}">
            <span class="status-dot"></span>
            <span class="status-label">${label}</span>
          </span>
        </div>
        <div class="home-channel-film">${filmInfo}</div>
      </button>
    `;
  }
  html += '</div>';
  return html;
}

function getChannelFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get('channel');
}

function setChannelInUrl(channelName) {
  const url = new URL(window.location);
  if (channelName) {
    url.searchParams.set('channel', channelName);
  } else {
    url.searchParams.delete('channel');
  }
  window.history.pushState({}, '', url);
}

function handleChannelChange() {
  const channel = getChannelFromUrl();
  if (channel) {
    loadChannel(channel);
  } else {
    // Home - sem canal selecionado
    destroyPlayer();
    channelData = null;
    currentChannel = null;
    isPlaying = false;
    overlay.querySelector('h1').textContent = 'VTV';
    overlayText.textContent = 'Selecione um canal';

    // Remove elementos dinâmicos do overlay
    const existingTable = overlay.querySelector('.upcoming-table');
    if (existingTable) existingTable.remove();
    const existingCards = overlay.querySelector('.home-channels');
    if (existingCards) existingCards.remove();

    // Adiciona cards de canais
    const cardsHtml = renderHomeChannels();
    if (cardsHtml) {
      overlayText.insertAdjacentHTML('afterend', cardsHtml);
    }

    btnPlay.classList.add('hidden');
    overlay.classList.remove('hidden');
    nowPlaying.classList.add('hidden');
    updateActiveChannel();
    if (updateInterval) clearInterval(updateInterval);
  }
}

// Play button
btnPlay.addEventListener('click', startPlayback);

// Toggle sidebar
btnToggle.addEventListener('click', openSidebar);

// Close sidebar
btnCloseSidebar.addEventListener('click', closeSidebar);

// Fullscreen
btnFullscreen.addEventListener('click', toggleFullscreen);

// Resize (object-fit)
btnResize.addEventListener('click', toggleResize);

// Volume
volumeSlider.addEventListener('input', (e) => {
  player.volume = e.target.value / 100;
  player.muted = false;
  localStorage.setItem('vtv-volume', e.target.value);
});

// Schedule modal (EPG)
btnSchedule.addEventListener('click', openEPG);

// Now playing também abre EPG
nowPlaying.addEventListener('click', openEPG);

// Links e botões no overlay
overlay.addEventListener('click', (e) => {
  // Link "Ver programação completa"
  if (e.target.id === 'btn-upcoming-schedule') {
    e.preventDefault();
    openEPG();
  }

  // Card de canal na home
  const card = e.target.closest('.home-channel-card');
  if (card) {
    const channel = card.dataset.channel;
    setChannelInUrl(channel);
    handleChannelChange();
  }
});

btnCloseModal.addEventListener('click', () => {
  scheduleModal.classList.add('hidden');
});

scheduleModal.addEventListener('click', (e) => {
  if (e.target === scheduleModal) {
    scheduleModal.classList.add('hidden');
  }
});

// Click em canal ou programa no EPG
epgChannels.addEventListener('click', (e) => {
  const row = e.target.closest('.epg-channel-row');
  if (row) {
    const channel = row.dataset.channel;
    scheduleModal.classList.add('hidden');
    setChannelInUrl(channel);
    handleChannelChange();
  }
});

epgPrograms.addEventListener('click', (e) => {
  const program = e.target.closest('.epg-program');
  if (program) {
    const channel = program.dataset.channel;
    scheduleModal.classList.add('hidden');
    setChannelInUrl(channel);
    handleChannelChange();
  }
});

// URL change (popstate for back/forward navigation)
window.addEventListener('popstate', handleChannelChange);

// Channel click
channelList.addEventListener('click', (e) => {
  const link = e.target.closest('a');
  if (link) {
    e.preventDefault();
    const channel = link.dataset.channel;
    setChannelInUrl(channel);
    handleChannelChange();
  }
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (!scheduleModal.classList.contains('hidden')) {
      scheduleModal.classList.add('hidden');
    } else if (!sidebar.classList.contains('hidden')) {
      closeSidebar();
    }
  }
  if (e.key === 'f' || e.key === 'F') {
    toggleFullscreen();
  }
  if (e.key === 'm' || e.key === 'M') {
    openSidebar();
  }
});

// Detecta atividade do usuário
document.addEventListener('mousemove', setActive);
document.addEventListener('mousedown', setActive);
document.addEventListener('keydown', setActive);

// Mantém ativo enquanto hover nos controles, sidebar, nowPlaying ou btnToggle
function handleUIMouseEnter() {
  isHoveringUI = true;
  clearTimeout(idleTimeout);
}
function handleUIMouseLeave() {
  isHoveringUI = false;
  idleTimeout = setTimeout(setIdle, IDLE_DELAY);
}
playerControls.addEventListener('mouseenter', handleUIMouseEnter);
playerControls.addEventListener('mouseleave', handleUIMouseLeave);
sidebar.addEventListener('mouseenter', handleUIMouseEnter);
sidebar.addEventListener('mouseleave', handleUIMouseLeave);
nowPlaying.addEventListener('mouseenter', handleUIMouseEnter);
nowPlaying.addEventListener('mouseleave', handleUIMouseLeave);
btnToggle.addEventListener('mouseenter', handleUIMouseEnter);
btnToggle.addEventListener('mouseleave', handleUIMouseLeave);

// ============ Inicialização ============

async function init() {
  // Carrega preferências salvas
  loadResizePreference();
  loadVolumePreference();

  // Sincroniza horário com o servidor
  await syncServerTime();

  await loadChannelList();
  handleChannelChange();

  // Configura sincronização de scroll do EPG
  setupEPGScrollSync();

  // Ressincroniza horário a cada 5 minutos
  setInterval(syncServerTime, 5 * 60 * 1000);

  // Atualiza EPG a cada 30 segundos se estiver aberto
  setInterval(() => {
    if (!scheduleModal.classList.contains('hidden')) {
      renderEPG();
    }
  }, 30000);
}

init();
