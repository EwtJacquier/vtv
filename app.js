/**
 * VTV - Sistema de TV por streaming
 */

const CHANNELS_PATH = '/channels';
const HLS_PATH = '/movies_hls';

const DAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

let currentChannel = null;
let channelData = null;
let hls = null;
let updateInterval = null;
let isPlaying = false;
let idleTimeout = null;
const IDLE_DELAY = 3000; // 3 segundos

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

function getNowInTimezone(timezone) {
  const now = new Date();
  const str = now.toLocaleString('en-US', { timeZone: timezone });
  return new Date(str);
}

function getDayName(date) {
  return DAYS[date.getDay()];
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
  console.log(`Loading: ${m3u8Url} at offset ${startOffset}s`);

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

  const timezone = channelData.timezone || 'America/Sao_Paulo';
  const now = getNowInTimezone(timezone);
  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);

  const windows = channelData[dayName] || [];
  const programs = expandSchedule(windows);
  const current = findCurrentProgram(programs, nowSeconds);

  if (current) {
    const { program, offset } = current;
    const remaining = program.duration - offset;
    const title = program.id.replace(/_/g, ' ');
    nowPlayingText.textContent = `${title} • ${formatTime(remaining)} restante`;
    nowPlaying.classList.remove('hidden');

    if (remaining <= 1) {
      console.log('Program ending, will refresh...');
      setTimeout(() => syncToSchedule(), 2000);
    }
  } else {
    nowPlayingText.textContent = 'Fora do ar';
    nowPlaying.classList.remove('hidden');
  }
}

function syncToSchedule() {
  if (!channelData) return;

  const timezone = channelData.timezone || 'America/Sao_Paulo';
  const now = getNowInTimezone(timezone);
  const dayName = getDayName(now);
  const nowSeconds = getSecondsOfDay(now);

  const windows = channelData[dayName] || [];
  const programs = expandSchedule(windows);
  const current = findCurrentProgram(programs, nowSeconds);

  if (current) {
    const { program, offset } = current;
    loadVideo(program.id, offset);

    // Mostra overlay com botão play se ainda não clicou
    if (!isPlaying) {
      overlay.querySelector('h1').textContent = currentChannel;
      overlayText.textContent = program.id.replace(/_/g, ' ');
      btnPlay.classList.remove('hidden');
      overlay.classList.remove('hidden');
    } else {
      overlay.classList.add('hidden');
    }
  } else {
    destroyPlayer();
    overlay.querySelector('h1').textContent = currentChannel;
    overlayText.textContent = 'Fora do ar no momento';
    btnPlay.classList.add('hidden');
    overlay.classList.remove('hidden');
  }
}

// ============ Programação (modal) ============

function renderSchedule(dayOffset = 0) {
  if (!channelData) {
    scheduleContent.innerHTML = '<div class="schedule-empty">Selecione um canal primeiro</div>';
    return;
  }

  const timezone = channelData.timezone || 'America/Sao_Paulo';
  const now = getNowInTimezone(timezone);
  const targetDate = new Date(now);
  targetDate.setDate(targetDate.getDate() + dayOffset);

  const dayName = getDayName(targetDate);
  const nowSeconds = dayOffset === 0 ? getSecondsOfDay(now) : -1;

  const windows = channelData[dayName] || [];
  const programs = expandSchedule(windows);

  if (programs.length === 0) {
    scheduleContent.innerHTML = '<div class="schedule-empty">Sem programação para este dia</div>';
    return;
  }

  let html = '';
  for (const prog of programs) {
    const isCurrent = dayOffset === 0 && findCurrentProgram([prog], nowSeconds);
    const title = prog.id.replace(/_/g, ' ');
    const startTime = formatTimeHHMM(prog.start);
    const duration = formatTime(prog.fullDuration);

    html += `
      <div class="schedule-item ${isCurrent ? 'current' : ''}">
        <span class="time">${startTime}</span>
        <span class="title">${title}</span>
        <span class="duration">${duration}</span>
      </div>
    `;
  }

  scheduleContent.innerHTML = html;
}

// ============ Carregamento de canais ============

async function loadChannelList() {
  try {
    const knownChannels = ['anos90'];

    const channels = [];
    for (const name of knownChannels) {
      try {
        const res = await fetch(`${CHANNELS_PATH}/${name}.json`, { method: 'HEAD' });
        if (res.ok) channels.push(name);
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
  channelList.innerHTML = channels.map(name => `
    <li>
      <a href="#${name}" data-channel="${name}">${name}</a>
    </li>
  `).join('');

  updateActiveChannel();
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
    const res = await fetch(`${CHANNELS_PATH}/${name}.json`);
    if (!res.ok) throw new Error('Channel not found');

    channelData = await res.json();
    currentChannel = name;
    isPlaying = false; // Reset play state for new channel

    updateActiveChannel();
    syncToSchedule();

    if (updateInterval) clearInterval(updateInterval);
    updateInterval = setInterval(updateNowPlaying, 1000);
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
    btnPlay.classList.add('hidden');
    overlay.classList.remove('hidden');
    nowPlaying.classList.add('hidden');
    updateActiveChannel();
    if (updateInterval) clearInterval(updateInterval);
    // Menu aberto na home
    openSidebar();
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

btnCloseModal.addEventListener('click', () => {
  scheduleModal.classList.add('hidden');
});

scheduleModal.addEventListener('click', (e) => {
  if (e.target === scheduleModal) {
    scheduleModal.classList.add('hidden');
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
  await loadChannelList();
  handleHashChange();

  setInterval(() => {
    if (!scheduleModal.classList.contains('hidden')) {
      const activeTab = document.querySelector('.modal-tabs .tab.active');
      const dayOffset = activeTab?.dataset.tab === 'tomorrow' ? 1 : 0;
      renderSchedule(dayOffset);
    }
  }, 1000);
}

init();
