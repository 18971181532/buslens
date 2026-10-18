/* BusLens - waveform viewer and protocol workbench (no dependencies). */

(() => {
  'use strict';

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const fmtHex = (v, w) => (v === null || v === undefined ? '—'
    : '0x' + v.toString(16).toUpperCase().padStart(w || 2, '0'));

  function fmtTime(ns) {
    if (!isFinite(ns)) return '—';
    if (Math.abs(ns) < 1000) return ns.toFixed(ns < 10 ? 2 : 0) + ' ns';
    if (Math.abs(ns) < 1e6) return (ns / 1000).toFixed(ns < 1e5 ? 2 : 1) + ' µs';
    if (Math.abs(ns) < 1e9) return (ns / 1e6).toFixed(ns < 1e8 ? 2 : 1) + ' ms';
    return (ns / 1e9).toFixed(2) + ' s';
  }

  function fmtFreq(hz) {
    if (!hz) return '—';
    if (hz >= 1e6) return (hz / 1e6).toFixed(2) + ' MHz';
    if (hz >= 1e3) return (hz / 1e3).toFixed(1) + ' kHz';
    return hz.toFixed(0) + ' Hz';
  }

  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('on');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('on'), 2400);
  }

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    };
    const res = await fetch(path, opt);
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || ('HTTP ' + res.status));
    }
    return data;
  }

  // ------------------------------------------------------------------ state
  const state = {
    demos: [],
    result: null,
    source: { type: 'demo', id: 'i2c_eeprom' },
    protocol: 'i2c',
    config: {},
    view: { t0: 0, t1: 0 },
    full: { t0: 0, t1: 0 },
    cursorA: null,
    cursorB: null,
    hover: null,
    tab: 'tx',
    dragging: null,
  };

  // ------------------------------------------------------------- demo list
  function renderDemoList() {
    const host = $('#demoList');
    host.innerHTML = '';
    state.demos.forEach((d) => {
      const el = document.createElement('div');
      el.className = 'demo-item' + (state.source.type === 'demo' && state.source.id === d.id ? ' active' : '');
      el.innerHTML = '<span class="dot"></span><span><b>' + d.name +
        '</b><br><span class="meta">' + d.protocol.toUpperCase() + ' · ' +
        d.channels.join(' / ') + '</span></span>';
      el.addEventListener('click', () => {
        state.source = { type: 'demo', id: d.id };
        loadSource();
      });
      host.appendChild(el);
    });
  }

  // ------------------------------------------------------------------ load
  async function loadSource() {
    $('#summary').innerHTML = '<span class="chip">解码中…</span>';
    try {
      let payload;
      if (state.source.type === 'demo') {
        payload = { demo: state.source.id, config: state.config };
      } else if (state.source.type === 'text') {
        payload = { [state.source.kind]: state.source.text, config: state.config };
        if (state.protocolOverride) payload.protocol = state.protocolOverride;
      } else if (state.source.type === 'sim') {
        payload = state.source.payload;
      }
      const result = await api('/api/analyze', payload);
      applyResult(result);
    } catch (err) {
      $('#summary').innerHTML = '<span class="chip" style="color:var(--err)">解码失败：' + err.message + '</span>';
      toast('解码失败：' + err.message);
    }
  }

  async function runSimulation(payload) {
    $('#summary').innerHTML = '<span class="chip">仿真中…</span>';
    try {
      const result = await api('/api/simulate', payload);
      state.source = { type: 'sim', payload };
      state.protocolOverride = null;
      state.config = {};
      applyResult(result);
      state.demos.forEach(() => {});
      renderDemoList();
      toast('虚拟设备已生成新的捕获');
    } catch (err) {
      toast('仿真失败：' + err.message);
    }
  }

  function applyResult(result) {
    state.result = result;
    state.protocol = result.protocol;
    state.config = Object.assign({}, result.config || {});
    const cap = result.capture;
    state.full = { t0: cap.t0, t1: Math.max(cap.t1, cap.t0 + 1000) };
    state.view = { t0: state.full.t0, t1: state.full.t1 };
    // Start zoomed on the first decoded element: a full 400 kHz I2C capture
    // is a solid block of clocks at fit-all zoom, which hides the very thing
    // the tool is about.  "适应" always restores the whole capture.
    const anns = (result.annotations || []).slice().sort((a, b) => a.t0 - b.t0);
    if (anns.length) {
      const width = Math.max(anns[0].t1 - anns[0].t0, 500);
      const guess = Math.min(state.full.t1, state.full.t0 + Math.max(width * 10, 30_000));
      if (guess < state.full.t0 + (state.full.t1 - state.full.t0) * 0.6) {
        state.view = { t0: state.full.t0, t1: guess };
      }
    }
    state.cursorA = null;
    state.cursorB = null;
    state.hover = null;
    renderAll();
  }

  // ------------------------------------------------------------------ render
  function renderAll() {
    renderSummary();
    renderQuality();
    renderConfig();
    drawWave();
    renderDetail();
    $('#sourceName').textContent = state.result.source + ' · ' +
      (state.result.description || '');
  }

  function renderSummary() {
    const r = state.result;
    if (!r) return;
    const s = r.summary || {};
    const stats = r.stats || {};
    const items = [
      ['协议', r.protocol.toUpperCase()],
      ['速率', r.protocol === 'uart' ? fmtFreq(1e9 / (stats.bitTimeNs || 1))
        : fmtFreq(stats.clockHz || stats.bitRateHz || 0)],
      ['摘要', s.primary || '—'],
      ['质量', (r.quality.grade || '—') + ' · ' + (r.quality.score || 0)],
      ['时长', fmtTime(r.capture.t1 - r.capture.t0)],
    ];
    $('#summary').innerHTML = items.map(([k, v]) =>
      '<span class="chip">' + k + '<b>' + v + '</b></span>').join('') +
      (r.description ? '<span class="chip" style="max-width:520px">' +
        r.description + '</span>' : '');
  }

  function renderQuality() {
    const q = state.result ? state.result.quality : null;
    if (!q) return;
    $('#qualityPanel').innerHTML =
      '<div class="quality"><div class="grade ' + q.grade + '">' + q.grade + '</div>' +
      '<div class="info"><b>' + q.score.toFixed(1) + ' / 100</b><br>' +
      (q.reasons.length ? q.reasons.join('<br>') : '无异常：时序余量充足') +
      '</div></div>';
  }

  // ----------------------------------------------------------- config form
  function renderConfig() {
    const host = $('#protoConfig');
    const p = state.protocol;
    const cfg = state.config || {};
    const protocols = [['i2c', 'I²C'], ['spi', 'SPI'], ['uart', 'UART'],
      ['pwm', 'PWM'], ['ws2812', 'WS2812']];
    let html = '<label class="field"><span>协议</span><select id="cfgProtocol">' +
      protocols.map(([v, t]) => '<option value="' + v + '"' +
        (v === p ? ' selected' : '') + '>' + t + '</option>').join('') +
      '</select></label>';

    if (p === 'i2c') {
      html += '<label class="field"><span>时钟拉伸阈值 / µs</span>' +
        '<input type="number" id="cfgStretch" value="' +
        ((cfg.stretchThresholdNs || 50000) / 1000) + '" step="5"></label>';
    } else if (p === 'spi') {
      html += '<div class="row-2">' +
        '<label class="field"><span>CPOL</span><select id="cfgCpol">' +
        [0, 1].map((v) => '<option value="' + v + '"' +
          (Number(cfg.cpol || 0) === v ? ' selected' : '') + '>' + v + '</option>').join('') +
        '</select></label>' +
        '<label class="field"><span>CPHA</span><select id="cfgCpha">' +
        [0, 1].map((v) => '<option value="' + v + '"' +
          (Number(cfg.cpha || 0) === v ? ' selected' : '') + '>' + v + '</option>').join('') +
        '</select></label></div>' +
        '<div class="row-2"><label class="field"><span>字长</span>' +
        '<select id="cfgBits"><option>8</option><option>16</option></select></label>' +
        '<label class="field"><span>位序</span><select id="cfgOrder">' +
        '<option value="msb">MSB</option><option value="lsb">LSB</option></select></label></div>';
    } else if (p === 'uart') {
      html += '<label class="field"><span>波特率</span>' +
        '<input type="number" id="cfgBaud" value="' + (cfg.baud || '') +
        '" placeholder="留空自动检测"></label>' +
        '<label class="field"><span>校验</span><select id="cfgParity">' +
        [['none', '无'], ['even', '偶'], ['odd', '奇']].map(([v, t]) =>
          '<option value="' + v + '"' + (cfg.parity === v ? ' selected' : '') +
          '>' + t + '</option>').join('') + '</select></label>';
    } else if (p === 'ws2812') {
      html += '<label class="field"><span>判决门限 / ns</span>' +
        '<input type="number" id="cfgThreshold" value="' +
        (cfg.thresholdNs || 550) + '" step="25"></label>';
    }
    host.innerHTML = html;
  }

  function collectConfig() {
    const p = state.protocol;
    const cfg = Object.assign({}, state.config);
    if (p === 'i2c' && $('#cfgStretch')) {
      cfg.stretchNs = (parseFloat($('#cfgStretch').value) || 50) * 1000;
    }
    if (p === 'spi') {
      cfg.cpol = parseInt($('#cfgCpol').value, 10);
      cfg.cpha = parseInt($('#cfgCpha').value, 10);
      cfg.bits = parseInt($('#cfgBits').value, 10);
      cfg.order = $('#cfgOrder').value;
    }
    if (p === 'uart') {
      const baud = parseFloat($('#cfgBaud').value);
      cfg.baud = isFinite(baud) && baud > 0 ? Math.round(baud) : null;
      cfg.parity = $('#cfgParity').value;
    }
    if (p === 'ws2812' && $('#cfgThreshold')) {
      cfg.thresholdNs = parseFloat($('#cfgThreshold').value) || 550;
    }
    return cfg;
  }

  // ------------------------------------------------------------- waveform
  function setupCanvas(canvas, heightCss) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(320, canvas.parentElement.getBoundingClientRect().width);
    canvas.style.height = heightCss + 'px';
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(heightCss * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    ctx.textBaseline = 'middle';
    return { ctx, w, h: heightCss, dpr };
  }

  const KIND_STYLE = {
    byte: '--accent', word: '--accent-2', ack: '--ok', nack: '--err',
    start: '--warn', stop: '--warn', stretch: '--violet', pixel: '--accent-2',
    pulse: '--accent-2', error: '--err',
  };

  function drawWave() {
    const r = state.result;
    if (!r) return;
    const cap = r.capture;
    const chans = cap.channels;
    const rowH = chans.length <= 2 ? 112 : 58;
    const gutter = 84;
    const axisH = 26;
    const top = 8;
    const height = top + chans.length * rowH + axisH;
    const { ctx, w } = setupCanvas($('#waveCanvas'), height);

    const plotX = gutter;
    const plotW = Math.max(50, w - gutter - 12);
    const plotY = top;
    const plotH = chans.length * rowH;
    const t0 = state.view.t0;
    const t1 = state.view.t1;
    const sx = (t) => plotX + ((t - t0) / (t1 - t0)) * plotW;

    ctx.clearRect(0, 0, w, height);

    // row bands
    chans.forEach((ch, i) => {
      const y = plotY + i * rowH;
      ctx.fillStyle = i % 2 ? cssVar('--panel-2') : cssVar('--panel');
      ctx.fillRect(0, y, w, rowH);
      ctx.strokeStyle = cssVar('--line-2');
      ctx.beginPath();
      ctx.moveTo(0, y + rowH - 0.5);
      ctx.lineTo(w, y + rowH - 0.5);
      ctx.stroke();
    });

    // time grid
    const ticks = niceTicks(t0, t1, Math.max(4, Math.floor(plotW / 110)));
    ctx.save();
    ctx.strokeStyle = cssVar('--line-2');
    ctx.fillStyle = cssVar('--muted');
    ctx.textAlign = 'center';
    ticks.forEach((t) => {
      const x = Math.round(sx(t)) + 0.5;
      if (x < plotX || x > plotX + plotW) return;
      ctx.beginPath();
      ctx.moveTo(x, plotY);
      ctx.lineTo(x, plotY + plotH);
      ctx.stroke();
      ctx.fillText(axisTime(t), x, plotY + plotH + axisH / 2);
    });
    ctx.restore();

    // channels
    chans.forEach((ch, i) => {
      const rowY = plotY + i * rowH;
      const hiY = rowY + rowH * 0.46;
      const loY = rowY + rowH * 0.84;

      ctx.fillStyle = cssVar('--ink-2');
      ctx.textAlign = 'left';
      ctx.font = '11.5px ui-monospace, monospace';
      ctx.fillText(ch.name, 10, rowY + rowH / 2);
      ctx.fillStyle = cssVar('--muted');
      ctx.font = '9.5px ui-monospace, monospace';
      ctx.fillText(ch.transitions.length + ' edges', 10, rowY + rowH / 2 + 14);

      // waveform
      ctx.save();
      ctx.beginPath();
      ctx.rect(plotX, plotY, plotW, plotH);
      ctx.clip();
      ctx.strokeStyle = cssVar('--trace');
      ctx.lineWidth = 1.7;
      ctx.lineJoin = 'miter';
      const levelY = (v) => (v ? hiY : loY);

      let prevT = t0;
      let prevV = levelAt(ch, t0);
      const pts = [];
      pts.push([sx(prevT), levelY(prevV)]);
      for (let k = 0; k < ch.transitions.length; k++) {
        const [t, v] = ch.transitions[k];
        if (t < t0) { prevV = v; continue; }
        if (t > t1) break;
        pts.push([sx(t), levelY(prevV)]);
        pts.push([sx(t), levelY(v)]);
        prevV = v;
      }
      pts.push([sx(t1), levelY(prevV)]);

      // translucent band over every high segment
      ctx.save();
      ctx.fillStyle = cssVar('--accent');
      ctx.globalAlpha = 0.09;
      for (let k = 0; k < pts.length - 1; k++) {
        if (Math.abs(pts[k][1] - hiY) < 0.5) {
          const width = pts[k + 1][0] - pts[k][0];
          if (width > 0) ctx.fillRect(pts[k][0], hiY - 3, width, loY - hiY + 8);
        }
      }
      ctx.restore();

      ctx.beginPath();
      pts.forEach((p, idx) => (idx ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      ctx.stroke();
      ctx.restore();

      // low/high rails
      ctx.save();
      ctx.strokeStyle = cssVar('--line');
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(plotX, hiY); ctx.lineTo(plotX + plotW, hiY);
      ctx.moveTo(plotX, loY); ctx.lineTo(plotX + plotW, loY);
      ctx.stroke();
      ctx.restore();

      // annotations
      const anns = (r.annotations || []).filter((a) => a.channel === ch.name);
      anns.forEach((a) => {
        if (a.t1 < t0 || a.t0 > t1) return;
        const x0 = sx(Math.max(a.t0, t0));
        const x1 = sx(Math.min(a.t1, t1));
        const width = Math.max(1.5, x1 - x0);
        const color = cssVar(KIND_STYLE[a.kind] || '--accent');
        const boxY = rowY + 4;
        const boxH = 15;
        if (width < 14) {
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(x0, hiY - 2);
          ctx.lineTo(x0, loY + 2);
          ctx.stroke();
          return;
        }
        ctx.save();
        ctx.globalAlpha = 0.16;
        ctx.fillStyle = color;
        roundRect(ctx, x0 + 1, boxY, width - 2, boxH, 4);
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        roundRect(ctx, x0 + 1, boxY, width - 2, boxH, 4);
        ctx.stroke();
        if (width > 30) {
          ctx.fillStyle = color;
          ctx.textAlign = 'center';
          ctx.font = '9.5px ui-monospace, monospace';
          ctx.save();
          ctx.beginPath();
          ctx.rect(x0 + 2, boxY, width - 4, boxH);
          ctx.clip();
          ctx.fillText(a.label, (x0 + x1) / 2, boxY + boxH / 2);
          ctx.restore();
        }
        ctx.restore();
      });
    });

    // cursors
    [[state.cursorA, cssVar('--accent'), 'A'], [state.cursorB, cssVar('--warn'), 'B']]
      .forEach(([t, color, label]) => {
        if (t === null || t === undefined) return;
        if (t < t0 || t > t1) return;
        const x = Math.round(sx(t)) + 0.5;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(x, plotY);
        ctx.lineTo(x, plotY + plotH);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillRect(x - 8, plotY - 8, 16, 13);
        ctx.fillStyle = '#fff';
        ctx.textAlign = 'center';
        ctx.font = '9.5px ui-monospace, monospace';
        ctx.fillText(label, x, plotY - 1.5);
        ctx.restore();
      });

    // hover line
    if (state.hover !== null && state.hover >= t0 && state.hover <= t1) {
      const x = Math.round(sx(state.hover)) + 0.5;
      ctx.save();
      ctx.strokeStyle = cssVar('--muted');
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, plotY);
      ctx.lineTo(x, plotY + plotH);
      ctx.stroke();
      ctx.restore();
      updateReadout(state.hover);
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    const rr = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  function axisTime(ns) {
    if (Math.abs(ns) >= 1e6) return (ns / 1e6).toFixed(2) + 'ms';
    if (Math.abs(ns) >= 1e3) return (ns / 1e3).toFixed(1) + 'µs';
    return ns.toFixed(0) + 'ns';
  }

  function niceTicks(min, max, count) {
    const span = max - min;
    if (!(span > 0)) return [min];
    const raw = span / Math.max(2, count);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / mag;
    const step = mag * (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10);
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) {
      out.push(v);
    }
    return out;
  }

  function levelAt(ch, t) {
    const tr = ch.transitions;
    let lo = 0;
    let hi = tr.length - 1;
    let idx = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (tr[mid][0] <= t) { idx = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return idx < 0 ? ch.initial : tr[idx][1];
  }

  function updateReadout(t) {
    const chans = state.result.capture.channels;
    const levels = chans.map((c) => c.name + '=' + levelAt(c, t)).join('  ');
    let html = 't <b>' + fmtTime(t) + '</b>  ' + levels;
    if (state.cursorA !== null && state.cursorB !== null) {
      html += '   Δt <b>' + fmtTime(Math.abs(state.cursorB - state.cursorA)) + '</b>';
      const dt = Math.abs(state.cursorB - state.cursorA);
      if (dt > 0) html += '   1/Δt <b>' + fmtFreq(1e9 / dt) + '</b>';
    }
    $('#cursorReadout').innerHTML = html;
  }

  // ------------------------------------------------------------- interaction
  function bindWave() {
    const canvas = $('#waveCanvas');
    const GUTTER = 84;

    function timeAt(clientX) {
      const rect = canvas.getBoundingClientRect();
      const x = clamp(clientX - rect.left, GUTTER, rect.width - 12);
      const frac = (x - GUTTER) / Math.max(1, rect.width - GUTTER - 12);
      return state.view.t0 + frac * (state.view.t1 - state.view.t0);
    }

    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      const t = timeAt(e.clientX);
      const factor = e.deltaY < 0 ? 0.8 : 1.25;
      let span = (state.view.t1 - state.view.t0) * factor;
      const full = state.full.t1 - state.full.t0;
      span = clamp(span, 40, full * 1.2);
      const left = (t - state.view.t0) / (state.view.t1 - state.view.t0);
      let t0 = t - left * span;
      let t1 = t0 + span;
      if (t0 < state.full.t0) { t0 = state.full.t0; t1 = t0 + span; }
      if (t1 > state.full.t1) { t1 = state.full.t1; t0 = t1 - span; }
      state.view = { t0, t1 };
      drawWave();
    }, { passive: false });

    canvas.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      state.dragging = { x: e.clientX, t0: state.view.t0, t1: state.view.t1, moved: false };
    });

    window.addEventListener('mousemove', (e) => {
      if (state.dragging) {
        const dx = e.clientX - state.dragging.x;
        if (Math.abs(dx) > 2) {
          state.dragging.moved = true;
          const rect = canvas.getBoundingClientRect();
          const plotW = Math.max(1, rect.width - GUTTER - 12);
          const span = state.dragging.t1 - state.dragging.t0;
          const shift = -(dx / plotW) * span;
          let t0 = state.dragging.t0 + shift;
          let t1 = state.dragging.t1 + shift;
          if (t0 < state.full.t0) { t1 += state.full.t0 - t0; t0 = state.full.t0; }
          if (t1 > state.full.t1) { t0 -= t1 - state.full.t1; t1 = state.full.t1; }
          state.view = { t0, t1 };
          drawWave();
        }
      }
    });

    window.addEventListener('mouseup', (e) => {
      const overCanvas = e.target === canvas;
      if (state.dragging && !state.dragging.moved && overCanvas) {
        state.cursorA = timeAt(e.clientX);
        drawWave();
        updateReadout(state.cursorA);
      }
      state.dragging = null;
    });

    canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      state.cursorB = timeAt(e.clientX);
      drawWave();
    });

    canvas.addEventListener('mousemove', (e) => {
      if (!state.result) return;
      state.hover = timeAt(e.clientX);
      drawWave();
    });

    canvas.addEventListener('mouseleave', () => {
      state.hover = null;
      drawWave();
    });

    canvas.addEventListener('dblclick', () => {
      state.view = { t0: state.full.t0, t1: state.full.t1 };
      drawWave();
    });

    window.addEventListener('resize', () => { if (state.result) drawWave(); });
  }

  function zoomBy(factor) {
    const mid = (state.view.t0 + state.view.t1) / 2;
    let span = (state.view.t1 - state.view.t0) * factor;
    span = clamp(span, 40, state.full.t1 - state.full.t0);
    state.view = { t0: mid - span / 2, t1: mid + span / 2 };
    drawWave();
  }

  // ------------------------------------------------------------------ detail
  function renderDetail() {
    const r = state.result;
    if (!r) return;
    const hasRegs = (r.registers || []).length > 0;
    $$('#detailTabs button').forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === state.tab);
      if (b.dataset.tab === 'reg') b.disabled = !hasRegs && r.protocol !== 'i2c';
      if (b.dataset.tab === 'device') b.disabled = r.protocol !== 'i2c';
    });
    const body = $('#detailBody');
    if (state.tab === 'tx') body.innerHTML = tableForProtocol(r);
    else if (state.tab === 'reg') body.innerHTML = registerTable(r);
    else if (state.tab === 'stats') body.innerHTML = statsPanel(r);
    else if (state.tab === 'err') body.innerHTML = errorPanel(r);
    else body.innerHTML = devicePanel(r);
    bindDetailActions();
  }

  function timeCell(t) {
    return '<span class="mono muted">' + fmtTime(t) + '</span>';
  }

  function tableForProtocol(r) {
    if (r.protocol === 'i2c') return i2cTable(r);
    if (r.protocol === 'spi') return spiTable(r);
    if (r.protocol === 'uart') return uartTable(r);
    if (r.protocol === 'pwm') return pwmTable(r);
    return wsTable(r);
  }

  function i2cTable(r) {
    const rows = (r.decoded.transactions || []).map((tx) =>
      '<tr class="clickable" data-t="' + tx.t0 + '">' +
      '<td class="num">' + tx.index + '</td>' +
      '<td><span class="pill ' + (tx.read ? 'r' : 'w') + '">' +
      (tx.read ? 'R' : 'W') + '</span></td>' +
      '<td class="mono">' + (tx.address === null ? '—' : fmtHex(tx.address)) + '</td>' +
      '<td class="mono">' + (tx.bytes.length ? tx.bytes.map((b) => b.toString(16).toUpperCase().padStart(2, '0')).join(' ') : '—') + '</td>' +
      '<td><span class="pill ' + (tx.acked ? 'ack' : 'nack') + '">' +
      (tx.acked ? 'ACK' : 'NACK') + '</span></td>' +
      '<td class="num">' + tx.durationUs.toFixed(1) + ' µs</td>' +
      '<td class="num">' + timeCell(tx.t0) + '</td></tr>').join('');
    return '<table><thead><tr><th class="num">#</th><th>R/W</th><th>地址</th>' +
      '<th>数据</th><th>应答</th><th class="num">时长</th><th class="num">起始</th>' +
      '</tr></thead><tbody>' + (rows || '<tr><td colspan="7" class="empty">没有事务</td></tr>') +
      '</tbody></table>';
  }

  function spiTable(r) {
    const rows = (r.decoded.words || []).map((wd) =>
      '<tr class="clickable" data-t="' + wd.t0 + '">' +
      '<td class="num">' + wd.index + '</td>' +
      '<td class="mono">' + (wd.mosi === null ? '—' : fmtHex(wd.mosi)) + '</td>' +
      '<td class="mono">' + (wd.miso === null ? '—' : fmtHex(wd.miso)) + '</td>' +
      '<td class="num">' + wd.bits + '</td>' +
      '<td class="num">' + timeCell(wd.t0) + '</td></tr>').join('');
    return '<table><thead><tr><th class="num">#</th><th>MOSI</th><th>MISO</th>' +
      '<th class="num">位</th><th class="num">时间</th></tr></thead><tbody>' +
      rows + '</tbody></table>';
  }

  function uartTable(r) {
    let html = '';
    Object.entries(r.decoded.text || {}).forEach(([name, text]) => {
      html += '<div style="margin-bottom:10px"><b>' + name + '</b> ' +
        '<span class="mono">' + JSON.stringify(text) + '</span></div>';
    });
    const allFrames = r.decoded.frames || [];
    const frames = allFrames.slice(0, 60).map((f) =>
      '<tr class="clickable" data-t="' + f.t0 + '">' +
      '<td class="num">' + f.index + '</td>' +
      '<td class="mono">' + (f.value < 32 ? '0x' + f.value.toString(16).toUpperCase().padStart(2, '0')
        : (f.value >= 32 && f.value < 127 ? "'" + f.char + "'" : '—')) + '</td>' +
      '<td class="num">' + f.value + '</td>' +
      '<td><span class="pill ' + (f.errors.length ? 'err' : 'ack') + '">' +
      (f.errors.length ? f.errors.join(',') : 'OK') + '</span></td>' +
      '<td class="num">' + timeCell(f.t0) + '</td></tr>').join('');
    return html + '<table><thead><tr><th class="num">#</th><th>字符</th><th class="num">值</th>' +
      '<th>状态</th><th class="num">时间</th></tr></thead><tbody>' + frames + '</tbody></table>' +
      (allFrames.length > 60 ? '<p class="muted">仅显示前 60 帧，共 ' + allFrames.length + ' 帧</p>' : '');
  }

  function pwmTable(r) {
    const s = r.stats;
    return '<div class="kv">' +
      kv('频率', s.frequencyHz.toFixed(2) + ' Hz') +
      kv('周期', s.periodUs.toFixed(2) + ' µs') +
      kv('高电平', s.highUs.toFixed(2) + ' µs') +
      kv('占空比', s.dutyPct.toFixed(2) + ' %') +
      kv('抖动', s.frequencyJitterPct.toFixed(3) + ' %') +
      kv('脉冲数', s.pulses) +
      '</div>';
  }

  function wsTable(r) {
    const rows = ((r.decoded && r.decoded.pixels) || []).map((p, i) =>
      '<tr class="clickable" data-t="' + p.t0 + '">' +
      '<td class="num">' + (i + 1) + '</td>' +
      '<td><span class="swatch" style="background:' + p.hex + '"></span></td>' +
      '<td class="mono">' + p.hex + '</td>' +
      '<td class="num">' + p.r + '</td><td class="num">' + p.g + '</td>' +
      '<td class="num">' + p.b + '</td>' +
      '<td class="num">' + timeCell(p.t0) + '</td></tr>').join('');
    return '<table><thead><tr><th class="num">#</th><th>颜色</th><th>HEX</th>' +
      '<th class="num">R</th><th class="num">G</th><th class="num">B</th>' +
      '<th class="num">时间</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function registerTable(r) {
    const rows = (r.registers || []).map((row) =>
      '<tr class="clickable" data-t="' + row.t + '">' +
      '<td class="mono">' + row.address + '</td>' +
      '<td class="mono">' + row.register + '</td>' +
      '<td class="mono">' + row.value + '</td>' +
      '<td class="num">' + row.decimal + '</td>' +
      '<td><span class="pill ' + (row.source === 'write' ? 'w' : 'r') + '">' +
      (row.source === 'write' ? '写入' : '读回') + '</span></td></tr>').join('');
    return '<p class="muted" style="margin:0 0 8px">I²C 本身没有寄存器语义，' +
      '下表按「首字节 = 寄存器指针」的通用约定推断，仅作调试参考。</p>' +
      '<table><thead><tr><th>从机</th><th>寄存器</th><th>值</th><th class="num">十进制</th>' +
      '<th>来源</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5" class="empty">没有可推断的寄存器访问</td></tr>') +
      '</tbody></table>';
  }

  function kv(k, v) {
    return '<div class="item"><div class="k">' + k + '</div><div class="v">' + v + '</div></div>';
  }

  function statsPanel(r) {
    const s = r.stats || {};
    const items = [];
    Object.keys(s).forEach((k) => {
      if (k === 'gaps') return;
      let v = s[k];
      if (typeof v === 'number') v = Math.round(v * 1000) / 1000;
      items.push(kv(k, v === null ? '—' : v));
    });
    items.push(kv('通道数', r.capture.channels.length));
    items.push(kv('跳变数', r.capture.channels.reduce((a, c) => a + c.transitions.length, 0)));
    items.push(kv('信号质量', r.quality.score + ' / 100'));
    return '<div class="kv">' + items.join('') + '</div>';
  }

  function errorPanel(r) {
    if (!r.errors.length) return '<div class="empty">没有检测到协议错误</div>';
    return '<table><thead><tr><th>级别</th><th>说明</th><th class="num">时间</th></tr></thead><tbody>' +
      r.errors.map((e) => '<tr class="clickable" data-t="' + e.t + '">' +
        '<td><span class="pill ' + (e.level === 'warning' ? 'warn' : 'err') + '">' +
        (e.level === 'warning' ? '警告' : '错误') + '</span></td>' +
        '<td>' + e.message + '</td><td class="num">' + timeCell(e.t) + '</td></tr>').join('') +
      '</tbody></table>';
  }

  function devicePanel(r) {
    if (r.protocol !== 'i2c') return '<div class="empty">虚拟设备仅适用于 I²C</div>';
    return '<p class="muted" style="margin:0 0 10px">' +
      '在虚拟从机上发起一次真实的总线事务，服务端会生成波形并立即用同一套解码器还原——' +
      '这就是项目的闭环自检方式。</p>' +
      '<div class="plan">' +
      '<label class="field"><span>从机</span><select id="devSel">' +
      '<option value="eeprom">24C02 EEPROM</option>' +
      '<option value="tmp102">TMP102 传感器</option>' +
      '<option value="none">无（NACK）</option></select></label>' +
      '<label class="field"><span>地址</span><input id="devAddr" value="0x50"></label>' +
      '<label class="field"><span>寄存器</span><input id="devReg" value="0x10"></label>' +
      '<label class="field"><span>写入值</span><input id="devVal" value="0xAA"></label>' +
      '<div class="actions"><button class="btn primary" id="devWrite">写入</button>' +
      '<button class="btn" id="devRead">读取 4 字节</button></div></div>' +
      '<div id="devOut" style="margin-top:10px" class="muted"></div>';
  }

  function bindDetailActions() {
    $$('#detailBody tr.clickable').forEach((tr) => {
      tr.addEventListener('click', () => {
        const t = parseFloat(tr.dataset.t);
        if (!isFinite(t)) return;
        const span = (state.view.t1 - state.view.t0) / 4;
        state.view = { t0: Math.max(state.full.t0, t - span / 2), t1: Math.min(state.full.t1, t + span / 2) };
        state.cursorA = t;
        drawWave();
        updateReadout(t);
      });
    });

    const write = $('#devWrite');
    if (write) {
      const parseHex = (v) => {
        const s = String(v).trim();
        return s.toLowerCase().startsWith('0x') ? parseInt(s, 16) : parseInt(s, 10);
      };
      const payload = (type) => {
        const addr = parseHex($('#devAddr').value);
        const reg = parseHex($('#devReg').value);
        const val = parseHex($('#devVal').value);
        const plan = type === 'write'
          ? { transactions: [{ type: 'write', address: addr, data: [reg, val] }] }
          : { transactions: [{ type: 'write_read', address: addr, data: [reg], count: 4 }] };
        return { device: $('#devSel').value, clockHz: 400000, plan };
      };
      write.addEventListener('click', () => runSimulation(payload('write')));
      $('#devRead').addEventListener('click', () => runSimulation(payload('read')));
    }
  }

  // ------------------------------------------------------------------ export
  function download(name, text, type) {
    const blob = new Blob([text], { type: type || 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  function exportJson() {
    if (!state.result) return;
    download('buslens-' + state.result.protocol + '.json',
      JSON.stringify(state.result, null, 2));
    toast('已导出分析结果');
  }

  function exportVcd() {
    if (!state.result) return;
    const cap = state.result.capture;
    const ids = cap.channels.map((c, i) => String.fromCharCode(33 + i));
    const lines = ['$timescale 1 ns $end', '$scope module buslens $end'];
    cap.channels.forEach((c, i) => lines.push('$var wire 1 ' + ids[i] + ' ' + c.name + ' $end'));
    lines.push('$upscope $end', '$enddefinitions $end', '#0');
    cap.channels.forEach((c, i) => lines.push(c.initial + ids[i]));
    const merged = [];
    cap.channels.forEach((c, i) => c.transitions.forEach(([t, v]) => merged.push([t, v, i])));
    merged.sort((a, b) => a[0] - b[0] || a[2] - b[2]);
    let last = null;
    merged.forEach(([t, v, i]) => {
      if (last !== t) { lines.push('#' + Math.round(t)); last = t; }
      lines.push(v + ids[i]);
    });
    download('buslens-capture.vcd', lines.join('\n') + '\n', 'text/plain');
    toast('已导出 VCD');
  }

  // -------------------------------------------------------------------- boot
  function bind() {
    $('#applyConfig').addEventListener('click', () => {
      const sel = $('#cfgProtocol');
      if (sel && sel.value !== state.protocol) {
        state.protocolOverride = sel.value;
      }
      state.config = collectConfig();
      loadSource();
    });

    $('#fileInput').addEventListener('change', async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      const text = await file.text();
      const kind = file.name.toLowerCase().endsWith('.csv') ? 'csv' : 'vcd';
      state.source = { type: 'text', kind, text };
      state.protocolOverride = null;
      state.config = {};
      await loadSource();
      toast('已导入 ' + file.name);
      e.target.value = '';
    });

    $('#exportJson').addEventListener('click', exportJson);
    $('#exportVcd').addEventListener('click', exportVcd);
    $('#zoomIn').addEventListener('click', () => zoomBy(0.7));
    $('#zoomOut').addEventListener('click', () => zoomBy(1.4));
    $('#fitBtn').addEventListener('click', () => {
      state.view = { t0: state.full.t0, t1: state.full.t1 };
      drawWave();
    });

    $$('#detailTabs button').forEach((b) => {
      b.addEventListener('click', () => {
        if (b.disabled) return;
        state.tab = b.dataset.tab;
        renderDetail();
      });
    });

    $('#themeBtn').addEventListener('click', () => {
      const root = document.documentElement;
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
      localStorage.setItem('buslens-theme', root.dataset.theme);
      setTimeout(() => state.result && drawWave(), 30);
    });

    bindWave();
  }

  async function boot() {
    const saved = localStorage.getItem('buslens-theme');
    if (saved) document.documentElement.dataset.theme = saved;
    bind();
    try {
      const data = await api('/api/demos');
      state.demos = data.demos;
      renderDemoList();
      await loadSource();
    } catch (err) {
      toast('无法连接本地服务：' + err.message);
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
