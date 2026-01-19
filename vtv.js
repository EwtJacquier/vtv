/**
 * VTV - Sistema de TV por streaming
 */

const CHANNELS_PATH = '/channels';
const HLS_PATH = '/movies_hls';

// Python weekday: 0=Monday, 6=Sunday -> converter para JS
const DAYS_FROM_PYTHON = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

let currentChannel = null;
let channelData = null;
let allChannelsData = {}; // cache de dados de todos os canais
let hls = null;
let updateInterval = null;
let isPlaying = false;
let idleTimeout = null;
let serverTimeOffset = 0; // diferença entre servidor e navegador em ms
const IDLE_DELAY = 3000;

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
const btnFullscreen = document.getElementById('btn-fullscreen');
const btnCloseModal = document.getElementById('btn-close-modal');
const scheduleModal = document.getElementById('schedule-modal');
const scheduleContent = document.getElementById('schedule-content');
const nowPlaying = document.getElementById('now-playing');
const nowPlayingText = document.getElementById('now-playing-text');
const volumeSlider = document.getElementById('volume');
const playerControls = document.getElementById('player-controls');
const tabs = document.querySelectorAll('.modal-tabs .tab');

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
  const h = Math.floor(seconds / 3600) % 24;
  const m = Math.floor((seconds % 3600) / 60);
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
  }).join(' ');
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

function getDayName(date) {
  // Converte de JS weekday (0=Sunday) para nome do dia
  const jsDay = date.getDay(); // 0=Sunday, 1=Monday, ...
  const pythonDay = jsDay === 0 ? 6 : jsDay - 1; // converter para 0=Monday
  return DAYS_FROM_PYTHON[pythonDay];
}

function getSecondsOfDay(date) {
  return date.getHours() * 3600 + date.getMinutes() * 60 + date.getSeconds();
}

// ============ Lógica de programação ============

function expandSchedule(windows) {
  const programs = [];

  for (const window of windows) {
    if (!window.playlist || window.playlist.length === 0) continue;

    const startSec = parseTime(window.start);
    let currentTime = startSec;

    // Sem loop - cada item da playlist toca uma vez
    for (const item of window.playlist) {
      programs.push({
        id: item.id,
        start: currentTime % (24 * 3600),
        duration: item.duration,
        fullDuration: item.duration,
        windowName: window.name
      });

      currentTime += item.duration;
    }
  }

  return programs;
}

function findCurrentProgram(programs, nowSeconds) {
  for (let i = 0; i < programs.length; i++) {
    const prog = programs[i];
    const progEnd = (prog.start + prog.duration) % (24 * 3600);

    if (prog.start <= progEnd) {
      if (nowSeconds >= prog.start && nowSeconds < progEnd) {
        const offset = nowSeconds - prog.start;
        return { program: prog, offset, index: i };
      }
    } else {
      if (nowSeconds >= prog.start || nowSeconds < progEnd) {
        const offset = nowSeconds >= prog.start
          ? nowSeconds - prog.start
          : (24 * 3600 - prog.start) + nowSeconds;
        return { program: prog, offset, index: i };
      }
    }
  }
  return null;
}

function findNextProgram(channelData, now) {
  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);

  // Procura no mesmo dia
  const todayWindows = channelData[dayName] || [];
  const todayPrograms = expandSchedule(todayWindows);

  for (const prog of todayPrograms) {
    if (prog.start > nowSeconds) {
      return {
        program: prog,
        secondsUntil: prog.start - nowSeconds,
        isToday: true
      };
    }
  }

  // Procura no próximo dia
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowDayName = getDayName(tomorrow);
  const tomorrowWindows = channelData[tomorrowDayName] || [];
  const tomorrowPrograms = expandSchedule(tomorrowWindows);

  if (tomorrowPrograms.length > 0) {
    const firstProg = tomorrowPrograms[0];
    const secondsUntilMidnight = 24 * 3600 - nowSeconds;
    return {
      program: firstProg,
      secondsUntil: secondsUntilMidnight + firstProg.start,
      isToday: false
    };
  }

  return null;
}

// Retorna status do canal: 'live' (transmitindo), 'soon' (em breve), 'offline' (fora do ar)
function getChannelStatus(chData, now) {
  if (!chData) return { status: 'offline', label: 'Fora do ar' };

  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);
  const windows = chData[dayName] || [];
  const programs = expandSchedule(windows);
  const current = findCurrentProgram(programs, nowSeconds);

  if (current) {
    return { status: 'live', label: 'No ar' };
  }

  const next = findNextProgram(chData, now);
  if (next) {
    // Tem próximo programa agendado
    return { status: 'soon', label: 'Em breve' };
  }

  // Sem próxima playlist
  return { status: 'offline', label: 'Fora do ar' };
}

// Retorna lista dos próximos N vídeos (incluindo amanhã se necessário)
function getUpcomingVideos(chData, now, count = 3) {
  if (!chData) return [];

  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);
  const windows = chData[dayName] || [];
  const todayPrograms = expandSchedule(windows);

  const upcoming = [];

  // Encontra programa atual para saber o índice
  const current = findCurrentProgram(todayPrograms, nowSeconds);
  let startIndex = 0;

  if (current) {
    startIndex = current.index + 1; // começa do próximo
  } else {
    // Não há programa atual, procura o primeiro que ainda não começou
    for (let i = 0; i < todayPrograms.length; i++) {
      if (todayPrograms[i].start > nowSeconds) {
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

  // Se precisar de mais, pega de amanhã
  if (upcoming.length < count) {
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const tomorrowDayName = getDayName(tomorrow);
    const tomorrowWindows = chData[tomorrowDayName] || [];
    const tomorrowPrograms = expandSchedule(tomorrowWindows);

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

  const m3u8Url = `${HLS_PATH}/${videoId}/stream.m3u8`;

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
  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);

  const windows = channelData[dayName] || [];
  const programs = expandSchedule(windows);
  const current = findCurrentProgram(programs, nowSeconds);

  if (current) {
    const { program, offset } = current;
    const remaining = program.duration - offset;
    const title = program.id.replace(/_/g, ' ');
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
  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);

  const windows = channelData[dayName] || [];
  const programs = expandSchedule(windows);
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
  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);

  const windows = channelData[dayName] || [];
  const programs = expandSchedule(windows);
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
      overlayText.textContent = program.id.replace(/_/g, ' ');
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

// ============ Programação (modal) ============

function renderSchedule(dayOffset = 0) {
  const channelNames = Object.keys(allChannelsData);

  if (channelNames.length === 0) {
    scheduleContent.innerHTML = '<div class="schedule-empty">Nenhum canal disponível</div>';
    return;
  }

  const now = getServerNow();
  const targetDate = new Date(now);
  targetDate.setDate(targetDate.getDate() + dayOffset);

  const dayName = getDayName(targetDate);
  const nowSeconds = dayOffset === 0 ? getSecondsOfDay(now) : -1;

  let html = '<div class="schedule-grid">';

  for (const chName of channelNames) {
    const chData = allChannelsData[chName];
    const windows = chData[dayName] || [];
    const programs = expandSchedule(windows);
    const { status } = getChannelStatus(chData, now);

    html += `
      <div class="schedule-channel">
        <div class="schedule-channel-header">
          <span class="channel-title">${capitalize(chName)}</span>
          <button class="btn-watch" data-channel="${chName}">Assistir</button>
        </div>
        <div class="schedule-channel-items">
    `;

    if (programs.length === 0) {
      html += '<div class="schedule-empty-mini">Sem programação</div>';
    } else {
      for (const prog of programs) {
        const isCurrent = dayOffset === 0 && findCurrentProgram([prog], nowSeconds);
        const title = capitalize(prog.id.replace(/_/g, ' '));
        const startTime = formatTimeHHMM(prog.start);

        html += `
          <div class="schedule-item-mini ${isCurrent ? 'current' : ''}">
            <span class="time">${startTime}</span>
            <span class="title">${title}</span>
          </div>
        `;
      }
    }

    html += '</div></div>';
  }

  html += '</div>';
  scheduleContent.innerHTML = html;
}

// ============ Carregamento de canais ============

async function loadChannelList() {
  try {
    const knownChannels = ['nostalgia90','imaginarium','superhero','animetv','paradox','afterdark'];

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
        <a href="#${name}" data-channel="${name}">
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
  idleTimeout = setTimeout(setIdle, IDLE_DELAY);
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
    const dayName = getDayName(now);
    const nowSeconds = getSecondsOfDay(now);
    const windows = chData[dayName] || [];
    const programs = expandSchedule(windows);
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

function handleHashChange() {
  const hash = window.location.hash.slice(1);
  if (hash) {
    loadChannel(hash);
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

// Volume
volumeSlider.addEventListener('input', (e) => {
  player.volume = e.target.value / 100;
  player.muted = false;
});

// Schedule modal
btnSchedule.addEventListener('click', () => {
  renderSchedule(0);
  scheduleModal.classList.remove('hidden');
});

// Links e botões no overlay
overlay.addEventListener('click', (e) => {
  // Link "Ver programação completa"
  if (e.target.id === 'btn-upcoming-schedule') {
    e.preventDefault();
    renderSchedule(0);
    scheduleModal.classList.remove('hidden');
  }

  // Card de canal na home
  const card = e.target.closest('.home-channel-card');
  if (card) {
    const channel = card.dataset.channel;
    window.location.hash = channel;
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

// Botão "Assistir" no modal de programação
scheduleContent.addEventListener('click', (e) => {
  if (e.target.classList.contains('btn-watch')) {
    const channel = e.target.dataset.channel;
    scheduleModal.classList.add('hidden');
    window.location.hash = channel;
  }
});

// Tabs
tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    tabs.forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const dayOffset = tab.dataset.tab === 'tomorrow' ? 1 : 0;
    renderSchedule(dayOffset);
  });
});

// Hash change
window.addEventListener('hashchange', handleHashChange);

// Channel click
channelList.addEventListener('click', (e) => {
  if (e.target.tagName === 'A') {
    e.preventDefault();
    const channel = e.target.dataset.channel;
    window.location.hash = channel;
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

// Mantém ativo enquanto hover nos controles ou sidebar
playerControls.addEventListener('mouseenter', () => clearTimeout(idleTimeout));
playerControls.addEventListener('mouseleave', () => idleTimeout = setTimeout(setIdle, IDLE_DELAY));
sidebar.addEventListener('mouseenter', () => clearTimeout(idleTimeout));
sidebar.addEventListener('mouseleave', () => idleTimeout = setTimeout(setIdle, IDLE_DELAY));

// ============ Inicialização ============

async function init() {
  // Sincroniza horário com o servidor
  await syncServerTime();

  await loadChannelList();
  handleHashChange();

  // Ressincroniza horário a cada 5 minutos
  //setInterval(syncServerTime, 5 * 60 * 1000);

  setInterval(() => {
    if (!scheduleModal.classList.contains('hidden')) {
      const activeTab = document.querySelector('.modal-tabs .tab.active');
      const dayOffset = activeTab?.dataset.tab === 'tomorrow' ? 1 : 0;

      // Preserva posição do scroll de cada canal antes de atualizar
      const scrollPositions = {};
      document.querySelectorAll('.schedule-channel-items').forEach((el, i) => {
        scrollPositions[i] = el.scrollTop;
      });
      const mainScroll = scheduleContent.scrollTop;

      renderSchedule(dayOffset);

      // Restaura posição do scroll
      scheduleContent.scrollTop = mainScroll;
      document.querySelectorAll('.schedule-channel-items').forEach((el, i) => {
        if (scrollPositions[i]) el.scrollTop = scrollPositions[i];
      });
    }
  }, 1000);
}

init();
