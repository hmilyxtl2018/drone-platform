import * as THREE from './lib/three.module.js';

const STAGES = [
  {key: 'source', label: '来源采集', color: '#55c6e8'},
  {key: 'candidate', label: '候选提取', color: '#e7b457'},
  {key: 'review', label: '人工审核', color: '#d88b55'},
  {key: 'published', label: '正式发布', color: '#64c28c'},
];

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

function toNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const match = String(value ?? '').replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

function searchNumber(value, keys, depth = 0) {
  if (depth > 5 || value == null) return null;
  if (Array.isArray(value)) {
    for (const item of value) { const found = searchNumber(item, keys, depth + 1); if (found != null) return found; }
    return null;
  }
  if (typeof value !== 'object') return null;
  for (const [key, item] of Object.entries(value)) {
    const normalized = key.toLowerCase().replace(/[_-]/g, '');
    if (keys.some(candidate => normalized.includes(candidate))) {
      const found = toNumber(item);
      if (found != null) return found;
    }
    const nested = searchNumber(item, keys, depth + 1);
    if (nested != null) return nested;
  }
  return null;
}

function compact(value, fallback = '未记录') {
  if (value == null || value === '') return fallback;
  if (Array.isArray(value)) return value.slice(0, 3).join('、') || fallback;
  if (typeof value === 'object') return Object.values(value).slice(0, 3).join('、') || fallback;
  return String(value);
}

function attributeValue(data, key) {
  const entry = data.attributes?.[key];
  if (entry == null) return null;
  if (Array.isArray(entry)) return entry[0]?.value ?? entry[0]?.normalizedValue ?? entry[0] ?? null;
  if (typeof entry !== 'object') return entry;
  const values = Array.isArray(entry.values) ? entry.values : [];
  return values[0]?.value ?? values[0]?.normalizedValue ?? values[0] ?? entry.value ?? entry.normalizedValue ?? null;
}

function componentEntries(data) {
  const rows = Array.isArray(data.assembly) ? data.assembly : [];
  const entries = [];
  const seen = new Set();
  const add = (name, type, relation, source = {}) => {
    const label = compact(name, '未命名部件');
    const key = `${type}|${label}`;
    if (seen.has(key)) return;
    seen.add(key);
    entries.push({name: label, type, relation: relation || 'partOf', quantity: source.quantity, status: source.record_state || source.review_status || source.status || 'published'});
  };
  rows.forEach(row => add(row.part_name || row.name || row.componentName, /ground|control station|地面|指挥/i.test(String(row.part_name || row.name || '')) ? 'ground' : 'payload', row.relation, row));
  const sensor = attributeValue(data, 'sensor-payload');
  if (sensor) add(sensor, 'sensor', 'sensorSuite', {status: data.attributes?.['sensor-payload']?.state || 'reported'});
  const weapons = attributeValue(data, 'primary-weapons');
  if (weapons) add(weapons, 'payload', 'missionPayload', {status: data.attributes?.['primary-weapons']?.state || 'reported'});
  if (!entries.length) add('机体结构', 'airframe', 'airframe', {status: 'unknown'});
  return entries.slice(0, 10);
}

function componentState(entry, data) {
  if (entry.status === 'conflict' || data.factorGraph?.inference?.status === 'conflict') return 'conflict';
  if (entry.status === 'accepted-not-published') return 'pending';
  if (['under-review', 'needs-evidence', 'pending', 'reported'].includes(String(entry.status))) return 'pending';
  if (['published', 'accepted'].includes(String(entry.status))) return 'published';
  return 'unknown';
}

function statusFromData(data) {
  const candidates = data.candidates || [];
  const graphStatus = data.factorGraph?.inference?.status || data.factorGraph?.status || data.graphStatus;
  if (graphStatus === 'conflict' || candidates.some(row => row.status === 'conflict')) return {key: 'conflict', label: '存在冲突', color: '#e37575'};
  if (candidates.some(row => ['under-review', 'needs-evidence'].includes(row.status)) || graphStatus === 'pending-review' || graphStatus === 'needs-evidence') return {key: 'pending', label: '待审核', color: '#e7b457'};
  if (candidates.some(row => row.status === 'published') || graphStatus === 'complete') return {key: 'published', label: '已发布', color: '#64c28c'};
  return {key: 'unknown', label: '数据待补', color: '#8da39b'};
}

function metricsFromData(data) {
  const attributes = data.attributes || {};
  const length = searchNumber(attributes, ['length', '机长']) || 3.4;
  const wingspan = searchNumber(attributes, ['wingspan', '翼展']) || 2.2;
  const payload = searchNumber(attributes, ['payloadcapacity', 'payloadkg', '载荷']) || 0;
  const speed = searchNumber(attributes, ['maxspeed', 'speed', '速度']);
  return {
    length: clamp(length / 3.4, 0.72, 2.1),
    wingspan: clamp(wingspan / 2.2, 0.7, 2.2),
    payload: clamp((payload || 0) / 100, 0, 2),
    speed,
  };
}

function partState(data, kind) {
  const candidates = data.candidates || [];
  const rows = data.assembly || [];
  if (kind === 'airframe' && rows.length) return 'published';
  if (data.factorGraph?.inference?.status === 'conflict') return 'conflict';
  if (candidates.some(row => ['under-review', 'needs-evidence'].includes(row.status))) return 'pending';
  return 'unknown';
}

function stageForData(data) {
  const candidates = data.candidates || [];
  if (candidates.some(row => row.status === 'published')) return 3;
  if (candidates.some(row => row.status === 'accepted')) return 2;
  if (candidates.length || data.factorGraph?.inference) return 1;
  if ((data.sources || []).length || data.snapshotCount) return 0;
  return 0;
}

function colorForState(state, fallback) {
  if (state === 'conflict') return '#e37575';
  if (state === 'pending') return '#e7b457';
  if (state === 'published') return '#64c28c';
  return fallback;
}

function material(color, opacity = 1, emissive = 0) {
  return new THREE.MeshStandardMaterial({color, roughness: 0.52, metalness: 0.32, transparent: opacity < 1, opacity, emissive: color, emissiveIntensity: emissive});
}

function buildDrone(scene, data, stage, explodeFactor = 0.28) {
  const root = new THREE.Group();
  const metrics = metricsFromData(data);
  const base = statusFromData(data);
  const bodyColor = colorForState(base.key, '#b8c8ce');
  const dark = material('#2d3c45');
  const body = material(bodyColor, stage >= 1 ? 0.96 : 0.78, base.key === 'conflict' ? 0.32 : 0.04);
  const wing = material(stage >= 2 ? '#7ac8b1' : '#7396a8', stage >= 3 ? 0.98 : 0.82, stage === 2 ? 0.18 : 0.02);
  const sensor = material(stage === 2 ? '#e7b457' : '#5cb8e5', 1, stage === 2 ? 0.5 : 0.15);
  const L = 3.4 * metrics.length;
  const W = 2.2 * metrics.wingspan;

  const fuselage = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.36, L, 24), body);
  fuselage.rotation.z = Math.PI / 2;
  fuselage.position.y = 0.25;
  root.add(fuselage);
  const nose = new THREE.Mesh(new THREE.ConeGeometry(0.36, 0.78, 24), body);
  nose.rotation.z = -Math.PI / 2;
  nose.position.set(L / 2 + 0.35, 0.25, 0);
  root.add(nose);
  const wingMesh = new THREE.Mesh(new THREE.BoxGeometry(L * 0.55, 0.07, W), wing);
  wingMesh.position.set(-0.05, 0.08, 0);
  root.add(wingMesh);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(L * 0.22, 0.06, W * 0.52), wing);
  tail.position.set(-L * 0.36, 0.28, 0);
  root.add(tail);
  const fin = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.4, 0.06), wing);
  fin.position.set(-L * 0.33, 0.5, 0);
  fin.rotation.z = -0.18;
  root.add(fin);
  const engine = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.14, 0.52, 16), dark);
  engine.rotation.z = Math.PI / 2;
  engine.position.set(-L * 0.32, 0.08, 0);
  root.add(engine);
  const sensorPod = new THREE.Mesh(new THREE.SphereGeometry(0.18, 18, 12), sensor);
  sensorPod.position.set(L * 0.23, -0.04, 0);
  sensorPod.name = 'sensor';
  root.add(sensorPod);
  const payloadBox = new THREE.Mesh(new THREE.BoxGeometry(0.42 + metrics.payload * 0.18, 0.2 + metrics.payload * 0.08, 0.58 + metrics.payload * 0.16), dark);
  payloadBox.position.set(-0.12, -0.24, 0);
  payloadBox.name = 'payload';
  root.add(payloadBox);

  const assemblyParts = [];
  const componentRows = componentEntries(data);
  componentRows.forEach((entry, index) => {
    const state = componentState(entry, data);
    const color = colorForState(state, entry.type === 'ground' ? '#4c6974' : entry.type === 'sensor' ? '#5cb8e5' : '#a1b9b6');
    const partMaterial = material(color, stage >= 1 ? 0.95 : 0.72, state === 'conflict' ? 0.28 : 0.08);
    const ground = entry.type === 'ground';
    const sensorLike = entry.type === 'sensor';
    const mesh = sensorLike
      ? new THREE.Mesh(new THREE.SphereGeometry(0.16 + Math.min(0.08, index * 0.01), 16, 10), partMaterial)
      : new THREE.Mesh(new THREE.BoxGeometry(ground ? 0.58 : 0.34, ground ? 0.28 : 0.22, ground ? 0.42 : 0.34), partMaterial);
    const side = index % 2 === 0 ? 1 : -1;
    const row = Math.floor(index / 2);
    const basePosition = ground
      ? new THREE.Vector3(-0.7 + row * 0.72, -0.38, side * 0.72)
      : new THREE.Vector3((index % 3 - 1) * 0.64, -0.16 - row * 0.04, side * (0.48 + row * 0.08));
    mesh.position.copy(basePosition);
    mesh.position.x += side * explodeFactor * (0.5 + row * 0.12);
    mesh.position.y += explodeFactor * (0.3 + row * 0.18);
    mesh.position.z += side * explodeFactor * 0.85;
    mesh.name = `assembly-${index + 1}`;
    root.add(mesh);
    assemblyParts.push({mesh, entry, basePosition});
  });

  const grid = new THREE.GridHelper(7.2, 18, '#2f5662', '#1d3038');
  grid.position.y = -0.55;
  scene.add(grid);
  const platform = new THREE.Mesh(new THREE.BoxGeometry(5.8, 0.08, 2.9), material('#132931', 0.92, 0.02));
  platform.position.y = -0.53;
  platform.name = 'assembly-platform';
  scene.add(platform);
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.58, 0.62, 48), material(base.color, 0.8, 0.45));
  ring.rotation.x = -Math.PI / 2;
  ring.position.set(sensorPod.position.x, -0.48, sensorPod.position.z);
  ring.name = 'pulse-ring';
  scene.add(ring);
  root.userData = {metrics, base, ring, grid, platform, sensorPod, payloadBox, assemblyParts, parts: {fuselage, wingMesh, tail, fin, engine, sensorPod, payloadBox}};
  scene.add(root);
  return root;
}

function labelsMarkup(data, stage) {
  const base = statusFromData(data);
  const factor = data.factorGraph?.inference;
  const gaps = factor?.gaps?.length || 0;
  const conflicts = factor?.conflicts?.length || 0;
  const labels = [
    {key: 'airframe', text: '机体 / 构型', value: `${(data.assembly || []).length || '—'} 项`, x: 10, y: 22},
    {key: 'sensor', text: '传感器 / 任务系统', value: compact(data.sensor || attributeValue(data, 'sensor-payload'), '待补证'), x: 68, y: 18},
    {key: 'payload', text: '载荷 / 挂载', value: compact(data.payload || attributeValue(data, 'payload-capacity'), '条件未记载'), x: 68, y: 68},
    {key: 'evidence', text: '证据状态', value: `${base.label}${conflicts ? ` · 冲突 ${conflicts}` : gaps ? ` · 缺口 ${gaps}` : ''}`, x: 9, y: 73},
  ];
  return labels.map(row => `<div class="model-callout model-callout-${row.key}" style="--callout-x:${row.x}%;--callout-y:${row.y}%"><span>${row.text}</span><b>${row.value}</b><i></i></div>`).join('');
}

function assemblyLabelsMarkup(data) {
  const entries = componentEntries(data).slice(0, 8);
  return entries.map((entry, index) => {
    const side = index % 2 === 0 ? 'left' : 'right';
    const y = 31 + Math.floor(index / 2) * 13;
    const state = componentState(entry, data);
    const stateLabel = state === 'published' ? '已发布' : state === 'pending' ? '待审核' : state === 'conflict' ? '有冲突' : '待补证';
    return `<div class="model-part-label model-part-label-${side}" style="--part-y:${y}%"><span>${String(index + 1).padStart(2, '0')}</span><div><b>${compact(entry.name, '未命名部件')}</b><small>${compact(entry.relation, '组成关系')} · ${stateLabel}</small></div><i></i></div>`;
  }).join('');
}

export function mountDynamicUavViewer(host, initial = {}) {
  if (!host) return null;
  host.__dynamicViewer?.dispose?.();
  host.innerHTML = '<div class="dynamic-model-root"><div class="dynamic-model-stage"></div><div class="dynamic-model-overlay"></div><div class="dynamic-model-controls"><button type="button" data-model-explode>收拢构型</button><button type="button" data-model-play>播放变化</button><input type="range" min="0" max="3" step="1" value="0" data-model-stage aria-label="数据状态阶段"><span data-model-stage-label>来源采集</span><button type="button" data-model-reset>复位</button></div><small class="dynamic-model-note">数据驱动装配示意 · 拖动旋转 · 滚轮缩放 · 当前结论仍受证据门禁约束</small></div>';
  const root = host.querySelector('.dynamic-model-root');
  const stageHost = root.querySelector('.dynamic-model-stage');
  const overlay = root.querySelector('.dynamic-model-overlay');
  const stageInput = root.querySelector('[data-model-stage]');
  const stageLabel = root.querySelector('[data-model-stage-label]');
  const playButton = root.querySelector('[data-model-play]');
  const data = {...initial};
  let stage = Number(initial.stage ?? stageForData(data));
  let playing = false;
  let exploded = true;
  let disposed = false;
  let frameId = 0;
  let lastTime = performance.now();
  let lastStageTime = performance.now();
  let drag = false;
  let lastX = 0;
  let lastY = 0;
  let scene;
  let camera;
  let renderer;
  let drone;

  const renderOverlay = () => {
    overlay.innerHTML = `${labelsMarkup(data, stage)}${assemblyLabelsMarkup(data)}<div class="model-stage-badge" style="--badge-color:${STAGES[stage].color}">${STAGES[stage].label}<small>${statusFromData(data).label}</small></div><div class="model-assembly-title"><b>构型组成</b><small>${componentEntries(data).length} 个已识别部件</small></div>`;
    stageLabel.textContent = STAGES[stage].label;
    stageInput.value = String(stage);
  };
  const rebuild = () => {
    if (!scene) return;
    const rotation = drone?.rotation?.clone?.() || null;
    if (drone) {
      if (drone.userData?.ring) scene.remove(drone.userData.ring);
      if (drone.userData?.grid) scene.remove(drone.userData.grid);
      if (drone.userData?.platform) scene.remove(drone.userData.platform);
      scene.remove(drone);
    }
    drone = buildDrone(scene, data, stage, exploded ? 0.68 : 0.18);
    if (rotation) drone.rotation.copy(rotation);
    renderOverlay();
  };

  try {
    scene = new THREE.Scene();
    scene.background = new THREE.Color('#081217');
    camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    camera.position.set(7.2, 4.1, 8.6);
    camera.lookAt(0, 0, 0);
    renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: 'high-performance'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.domElement.setAttribute('aria-label', '数据驱动无人机参数化模型');
    stageHost.append(renderer.domElement);
    scene.add(new THREE.HemisphereLight('#d7f5ff', '#16242d', 1.8));
    const key = new THREE.DirectionalLight('#ffffff', 2.2);
    key.position.set(4, 6, 5);
    scene.add(key);
    rebuild();
    const resize = () => {
      if (!renderer || !stageHost.clientWidth || !stageHost.clientHeight) return;
      const width = stageHost.clientWidth;
      const height = stageHost.clientHeight;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(stageHost);
    resize();
    const tick = now => {
      if (disposed) return;
      const delta = Math.min(0.05, (now - lastTime) / 1000);
      lastTime = now;
      if (drone && playing) drone.rotation.y += delta * 0.42;
      if (playing && now - lastStageTime > 2600) {
        stage = (stage + 1) % STAGES.length;
        lastStageTime = now;
        rebuild();
      }
      if (drone?.userData?.ring) {
        const pulse = 1 + Math.sin(now / 360) * 0.09;
        drone.userData.ring.scale.set(pulse, pulse, pulse);
        drone.userData.ring.material.opacity = 0.45 + (Math.sin(now / 360) + 1) * 0.16;
      }
      renderer.render(scene, camera);
      frameId = requestAnimationFrame(tick);
    };
    frameId = requestAnimationFrame(tick);
    stageInput.addEventListener('input', event => { stage = Number(event.target.value); rebuild(); });
    playButton.addEventListener('click', () => { playing = !playing; lastStageTime = performance.now(); playButton.textContent = playing ? '暂停变化' : '播放变化'; });
    root.querySelector('[data-model-explode]').addEventListener('click', event => { exploded = !exploded; event.currentTarget.textContent = exploded ? '收拢构型' : '展开构型'; rebuild(); });
    root.querySelector('[data-model-reset]').addEventListener('click', () => { drone.rotation.set(0, 0, 0); camera.position.set(7.2, 4.1, 8.6); camera.lookAt(0, 0, 0); stage = stageForData(data); exploded = true; root.querySelector('[data-model-explode]').textContent = '收拢构型'; rebuild(); });
    renderer.domElement.addEventListener('pointerdown', event => { drag = true; lastX = event.clientX; lastY = event.clientY; renderer.domElement.setPointerCapture(event.pointerId); });
    renderer.domElement.addEventListener('pointermove', event => { if (!drag || !drone) return; drone.rotation.y += (event.clientX - lastX) * 0.008; drone.rotation.x = clamp(drone.rotation.x + (event.clientY - lastY) * 0.004, -0.48, 0.48); lastX = event.clientX; lastY = event.clientY; });
    renderer.domElement.addEventListener('pointerup', () => { drag = false; });
    renderer.domElement.addEventListener('pointercancel', () => { drag = false; });
    renderer.domElement.addEventListener('wheel', event => { event.preventDefault(); camera.position.multiplyScalar(event.deltaY > 0 ? 1.08 : 0.93); camera.position.clampLength(3.2, 10); }, {passive: false});
    const viewer = {
      update(patch = {}) { Object.assign(data, patch); if (patch.stage != null) stage = Number(patch.stage); if (patch.exploded != null) exploded = Boolean(patch.exploded); rebuild(); },
      setStage(value) { stage = clamp(Number(value) || 0, 0, 3); rebuild(); },
      dispose() { disposed = true; cancelAnimationFrame(frameId); observer.disconnect(); renderer.dispose(); host.__dynamicViewer = null; },
    };
    host.__dynamicViewer = viewer;
    return viewer;
  } catch (error) {
    host.innerHTML = `<div class="dynamic-model-fallback"><b>参数化模型暂不可用</b><small>${String(error?.message || error)}</small><span>当前仍可查看右侧结构化数据与证据状态。</span></div>`;
    return null;
  }
}

