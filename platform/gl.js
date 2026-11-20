import * as THREE from "three";
import { OrbitControls } from "./lib/OrbitControls.js";
import { Water } from "./lib/jsm/objects/Water.js";
import { EffectComposer } from "./lib/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "./lib/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "./lib/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "./lib/jsm/postprocessing/OutputPass.js";
import { RGBELoader } from "./lib/jsm/loaders/RGBELoader.js";

const N_MAX = 6;
const K_MAX = 4;

function seed(n) {
  let a = n;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = seed(2026);

const root = document.getElementById("gl");
function viewSize() {
  return [root.clientWidth || window.innerWidth, root.clientHeight || window.innerHeight];
}
const [vw0, vh0] = viewSize();

const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
renderer.setSize(vw0, vh0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.18;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
root.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05070c);
scene.fog = new THREE.FogExp2(0x07090f, 0.00115);

const camera = new THREE.PerspectiveCamera(55, vw0 / vh0, 0.25, 4200);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.05;
controls.maxPolarAngle = Math.PI * 0.495;
controls.minDistance = 2.5;
controls.maxDistance = 1100;
controls.target.set(18, 8, 36);

function look(from, at) {
  camera.position.set(...from);
  controls.target.set(...at);
  controls.update();
}
look([-16, 4.1, -72], [24, 11, 28]);

scene.add(new THREE.HemisphereLight(0x6a7b94, 0x080a0e, 0.18));
const moon = new THREE.DirectionalLight(0xc9d6ea, 0.55);
moon.position.set(-220, 280, -90);
moon.castShadow = true;
moon.shadow.mapSize.set(2048, 2048);
moon.shadow.camera.near = 10;
moon.shadow.camera.far = 900;
moon.shadow.camera.left = -260;
moon.shadow.camera.right = 260;
moon.shadow.camera.top = 220;
moon.shadow.camera.bottom = -220;
moon.shadow.bias = -0.0002;
scene.add(moon);

const envScene = new THREE.Scene();
envScene.add(new THREE.Mesh(
  new THREE.SphereGeometry(12, 20, 12),
  new THREE.MeshBasicMaterial({ color: 0x0c141e, side: THREE.BackSide }),
));
const envMoon = new THREE.Mesh(new THREE.SphereGeometry(0.35, 12, 12), new THREE.MeshBasicMaterial({ color: 0xdce6f2 }));
envMoon.position.set(-4, 6, -3);
envScene.add(envMoon);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(envScene, 0.04).texture;
new RGBELoader().load("./lib/textures/night.hdr", (tex) => {
  tex.mapping = THREE.EquirectangularReflectionMapping;
  const env = pmrem.fromEquirectangular(tex).texture;
  scene.environment = env;
  scene.background = env;
  scene.backgroundIntensity = 0.28;
  scene.backgroundBlurriness = 0.08;
  tex.dispose();
});

function canvasTex(size, paint, srgb = true) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  paint(c.getContext("2d"), size);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const waterNormals = canvasTex(256, (ctx, s) => {
  const img = ctx.createImageData(s, s);
  for (let y = 0; y < s; y++) {
    for (let x = 0; x < s; x++) {
      const i = (y * s + x) * 4;
      const n1 = Math.sin(x * 0.11 + y * 0.07);
      const n2 = Math.cos(x * 0.05 - y * 0.13);
      img.data[i] = 128 + 90 * n1;
      img.data[i + 1] = 128 + 90 * n2;
      img.data[i + 2] = 255;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
}, false);
waterNormals.wrapS = waterNormals.wrapT = THREE.RepeatWrapping;

const asphalt = canvasTex(256, (ctx, s) => {
  const img = ctx.createImageData(s, s);
  const r = seed(11);
  for (let i = 0; i < img.data.length; i += 4) {
    const n = 28 + r() * 18;
    img.data[i] = n;
    img.data[i + 1] = n + 2;
    img.data[i + 2] = n + 4;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
});
asphalt.repeat.set(18, 10);

const concrete = canvasTex(256, (ctx, s) => {
  ctx.fillStyle = "#6a6862";
  ctx.fillRect(0, 0, s, s);
  const r = seed(4);
  for (let i = 0; i < 900; i++) {
    ctx.fillStyle = `rgba(0,0,0,${0.04 + r() * 0.08})`;
    ctx.fillRect(r() * s, r() * s, 2 + r() * 8, 1);
  }
});

const facade = canvasTex(512, (ctx, s) => {
  ctx.fillStyle = "#1c2228";
  ctx.fillRect(0, 0, s, s);
  const r = seed(19);
  const floors = 10;
  const cols = 7;
  const bw = s / cols;
  const bh = s / floors;
  for (let fy = 0; fy < floors; fy++) {
    for (let fx = 0; fx < cols; fx++) {
      const x = fx * bw + 10;
      const y = fy * bh + 8;
      const w = bw - 20;
      const h = bh - 16;
      ctx.fillStyle = "#0b0d10";
      ctx.fillRect(x, y, w, h);
      if (r() < 0.42) continue;
      const warm = r() > 0.25;
      ctx.fillStyle = warm ? `rgba(255, ${180 + r() * 50 | 0}, 90, 0.92)` : `rgba(160, 200, 230, 0.55)`;
      ctx.fillRect(x + 2, y + 2, w - 4, h - 4);
      ctx.fillStyle = "rgba(0,0,0,0.25)";
      ctx.fillRect(x + 2, y + 2, w - 4, (h - 4) * 0.35);
    }
  }
});

const rustMetal = new THREE.MeshStandardMaterial({ color: 0x6a5344, roughness: 0.55, metalness: 0.45 });
const steel = new THREE.MeshStandardMaterial({ color: 0x4c5550, roughness: 0.38, metalness: 0.62 });
const hullPaint = new THREE.MeshStandardMaterial({ color: 0x3a4240, roughness: 0.42, metalness: 0.35 });
const deckPaint = new THREE.MeshStandardMaterial({ color: 0x2e3432, roughness: 0.55, metalness: 0.2 });
const wetGround = new THREE.MeshStandardMaterial({
  map: asphalt,
  color: 0x1a1e22,
  roughness: 0.28,
  metalness: 0.18,
  envMapIntensity: 1.1,
});
const concMat = new THREE.MeshStandardMaterial({ map: concrete, roughness: 0.78, metalness: 0.05 });
const cityMat = new THREE.MeshStandardMaterial({
  map: facade,
  emissive: 0xffc27a,
  emissiveMap: facade,
  emissiveIntensity: 0.85,
  roughness: 0.7,
  metalness: 0.08,
});
const wareMat = new THREE.MeshStandardMaterial({ color: 0x3e433c, roughness: 0.82, metalness: 0.12 });

function add(geo, mat, x, y, z, parent = scene, shadow = true) {
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  m.castShadow = shadow;
  m.receiveShadow = true;
  parent.add(m);
  return m;
}

const starGeo = new THREE.BufferGeometry();
const starPos = new Float32Array(1500);
for (let i = 0; i < 500; i++) {
  const u = rnd() * Math.PI * 2;
  const v = rnd() * 0.7 + 0.05;
  const rr = 1600;
  starPos[i * 3] = rr * Math.sin(v) * Math.cos(u);
  starPos[i * 3 + 1] = rr * Math.cos(v) * 0.55 + 180;
  starPos[i * 3 + 2] = rr * Math.sin(v) * Math.sin(u);
}
starGeo.setAttribute("position", new THREE.BufferAttribute(starPos, 3));
scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xe8eef8, size: 1.6, sizeAttenuation: true })));

const moonBall = add(new THREE.SphereGeometry(14, 24, 16), new THREE.MeshBasicMaterial({ color: 0xe7eef6 }), -420, 260, -180, scene, false);
moonBall.material.fog = false;

const water = new Water(new THREE.PlaneGeometry(2800, 2000), {
  textureWidth: 512,
  textureHeight: 512,
  waterNormals,
  sunDirection: moon.position.clone().normalize(),
  sunColor: 0xb7c6dc,
  waterColor: 0x0a1822,
  distortionScale: 1.15,
  fog: true,
});
water.rotation.x = -Math.PI / 2;
water.position.set(-80, 0, -420);
water.material.uniforms.size.value = 0.35;
scene.add(water);

const land = add(new THREE.PlaneGeometry(900, 620), wetGround, 40, 0.02, 220);
land.rotation.x = -Math.PI / 2;
const quay = add(new THREE.BoxGeometry(220, 1.6, 28), concMat, 8, 0.8, 6);
const pier = add(new THREE.BoxGeometry(18, 1.2, 55), concMat, -6, 0.7, -22);

for (let i = 0; i < 8; i++) {
  add(new THREE.CylinderGeometry(0.18, 0.22, 0.7, 8), steel, -12 + i * 4.2, 1.55, -46);
}

function lamp(x, z, h = 8.2) {
  add(new THREE.CylinderGeometry(0.12, 0.18, h, 8), steel, x, h / 2, z);
  add(new THREE.BoxGeometry(1.6, 0.08, 0.18), steel, x + 0.6, h, z);
  add(
    new THREE.SphereGeometry(0.18, 12, 12),
    new THREE.MeshStandardMaterial({ color: 0xffe1a8, emissive: 0xffc56a, emissiveIntensity: 4.2, roughness: 0.3 }),
    x + 1.15,
    h - 0.15,
    z,
    scene,
    false,
  );
  const l = new THREE.PointLight(0xffc978, 48, 42, 2);
  l.position.set(x + 1.15, h - 0.2, z);
  scene.add(l);
}
for (let i = 0; i < 7; i++) lamp(-70 + i * 26, 12, 7.6);
lamp(14, -8, 7.2);
lamp(-22, -8, 7.2);

function crane(x, z) {
  const g = new THREE.Group();
  g.position.set(x, 0, z);
  add(new THREE.BoxGeometry(2.2, 38, 2.2), rustMetal, -6, 19, 0, g);
  add(new THREE.BoxGeometry(2.2, 38, 2.2), rustMetal, 6, 19, 0, g);
  add(new THREE.BoxGeometry(28, 1.8, 2.4), rustMetal, 4, 38.4, 0, g);
  add(new THREE.BoxGeometry(2.4, 6, 2.4), rustMetal, 0, 42, 0, g);
  add(new THREE.BoxGeometry(0.35, 16, 0.35), steel, 10, 30, 0, g);
  add(new THREE.BoxGeometry(3.2, 1.4, 2.2), steel, 10, 22, 0, g);
  g.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
  scene.add(g);
}
crane(-48, 2);
crane(-8, 2);
crane(32, 2);

const boxCols = [0x2f5a73, 0x8a4e24, 0x3d5a3a, 0x6b3030, 0x2c2e3a];
function stack(x, z, cols, rows) {
  for (let i = 0; i < cols; i++) {
    for (let j = 0; j < rows; j++) {
      const h = 1 + Math.floor(rnd() * 3);
      for (let k = 0; k < h; k++) {
        add(
          new THREE.BoxGeometry(6.1, 2.6, 2.45),
          new THREE.MeshStandardMaterial({ color: boxCols[(i + j + k) % 5], roughness: 0.55, metalness: 0.2 }),
          x + i * 6.3,
          1.4 + 0.9 + k * 2.62,
          z + j * 2.6,
        );
      }
    }
  }
}
stack(-78, 28, 8, 3);
stack(22, 32, 6, 3);

function shed(x, z, w, d, h) {
  add(new THREE.BoxGeometry(w, h, d), wareMat, x, h / 2 + 0.8, z);
  add(new THREE.BoxGeometry(w + 1.2, 0.45, d + 1.2), new THREE.MeshStandardMaterial({ color: 0x4a3a2e, roughness: 0.7 }), x, h + 0.95, z);
}
shed(-62, 58, 28, 16, 9);
shed(-22, 62, 34, 18, 11);
shed(28, 56, 24, 14, 8);

for (let i = 0; i < 22; i++) {
  const x = -40 + rnd() * 220;
  const z = 88 + rnd() * 210;
  if (Math.hypot(x - 48, z - 118) < 28) continue;
  if (Math.hypot(x - 168, z - 248) < 36) continue;
  const w = 10 + rnd() * 16;
  const d = 9 + rnd() * 14;
  const h = 14 + rnd() * 32;
  add(new THREE.BoxGeometry(w, h, d), cityMat, x, h / 2, z);
}

const sam = { x: 48, z: 118 };
add(new THREE.CylinderGeometry(16, 16, 0.5, 28), concMat, sam.x, 0.35, sam.z);

function tel(x, z, yaw) {
  const g = new THREE.Group();
  g.position.set(x, 0, z);
  g.rotation.y = yaw;
  add(new THREE.BoxGeometry(2.8, 1.15, 8.4), new THREE.MeshStandardMaterial({ color: 0x4a5a3c, roughness: 0.55 }), 0, 1.2, 0, g);
  add(new THREE.BoxGeometry(2.5, 1.4, 2.6), new THREE.MeshStandardMaterial({ color: 0x3a4a32 }), 0, 2.2, -2.4, g);
  const tubes = new THREE.Group();
  tubes.position.set(0, 2.0, 1.1);
  tubes.rotation.x = -0.95;
  for (let i = 0; i < 4; i++) add(new THREE.CylinderGeometry(0.28, 0.28, 6.2, 10), steel, (i - 1.5) * 0.7, 2.4, 0, tubes);
  g.add(tubes);
  scene.add(g);
}
tel(sam.x - 6, sam.z - 5, 0.45);
tel(sam.x + 6, sam.z - 4, 0.2);
tel(sam.x - 5, sam.z + 6, 0.7);
tel(sam.x + 7, sam.z + 5, 0.5);

const radarVan = new THREE.Group();
radarVan.position.set(sam.x - 11, 0, sam.z + 8);
add(new THREE.BoxGeometry(2.6, 2.1, 5.4), new THREE.MeshStandardMaterial({ color: 0x44543c }), 0, 1.4, 0, radarVan);
const dish = add(new THREE.CylinderGeometry(2.4, 2.4, 0.18, 24), new THREE.MeshStandardMaterial({ color: 0x8aa8a4, metalness: 0.55, roughness: 0.3, emissive: 0x102018, emissiveIntensity: 0.4 }), 0, 5.4, 0, radarVan);
dish.rotation.x = 0.55;
add(new THREE.CylinderGeometry(0.18, 0.18, 3.4, 8), steel, 0, 3.4, 0, radarVan);
scene.add(radarVan);
const samLight = new THREE.PointLight(0xcfe4f2, 30, 36, 2);
samLight.position.set(sam.x, 6, sam.z);
scene.add(samLight);

const bmd = { x: 168, z: 248 };
add(new THREE.BoxGeometry(28, 0.5, 22), concMat, bmd.x, 0.3, bmd.z);
const face = add(new THREE.BoxGeometry(1.4, 12, 9), new THREE.MeshStandardMaterial({ color: 0x8aa9a6, metalness: 0.5, roughness: 0.25, emissive: 0x1a3330, emissiveIntensity: 0.45 }), bmd.x - 4, 8, bmd.z);
face.rotation.z = 0.22;
add(new THREE.CylinderGeometry(1.1, 1.3, 11, 12), steel, bmd.x + 6, 6, bmd.z - 4);
add(new THREE.CylinderGeometry(1.1, 1.3, 11, 12), steel, bmd.x + 8, 6, bmd.z + 3);

const c2p = { x: 258, z: 328 };
add(new THREE.BoxGeometry(18, 10, 14), cityMat, c2p.x, 5.1, c2p.z);
add(new THREE.CylinderGeometry(0.25, 0.3, 22, 8), steel, c2p.x - 4, 16, c2p.z);
const c2dish = add(new THREE.CylinderGeometry(2.1, 2.1, 0.16, 20), new THREE.MeshStandardMaterial({ color: 0xc5d0c8, metalness: 0.5, roughness: 0.3 }), c2p.x - 4, 26, c2p.z);
c2dish.rotation.x = 1.0;
const c2Light = new THREE.PointLight(0xffd9a0, 40, 50, 2);
c2Light.position.set(c2p.x, 8, c2p.z);
scene.add(c2Light);

const goldMat = new THREE.LineBasicMaterial({ color: 0xe2c36a, transparent: true, opacity: 0.85 });
const dimMat = new THREE.LineBasicMaterial({ color: 0x5a2e28, transparent: true, opacity: 0.55 });
function link(a, b) {
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...a), new THREE.Vector3(...b)]), goldMat);
  scene.add(line);
  return line;
}
const links = [
  link([sam.x, 7, sam.z], [bmd.x, 10, bmd.z]),
  link([bmd.x, 10, bmd.z], [c2p.x, 18, c2p.z]),
  link([sam.x, 7, sam.z], [c2p.x, 18, c2p.z]),
];

function destroyer() {
  const g = new THREE.Group();
  const len = 142;
  const beam = 16;
  const hullGeo = new THREE.BoxGeometry(beam, 7.2, len, 1, 1, 12);
  const pos = hullGeo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const z = pos.getZ(i);
    const y = pos.getY(i);
    const x = pos.getX(i);
    const t = (z + len / 2) / len;
    const taper = t > 0.68 ? 1 - ((t - 0.68) / 0.32) * 0.88 : t < 0.12 ? 0.78 + t * 1.8 : 1;
    pos.setX(i, x * Math.max(taper, 0.12));
    if (y < 0) pos.setY(i, y * (0.55 + 0.45 * taper));
  }
  hullGeo.computeVertexNormals();
  add(hullGeo, hullPaint, 0, 3.2, 0, g);
  add(new THREE.BoxGeometry(14, 0.4, 128), deckPaint, 0, 6.85, -4, g);
  add(new THREE.BoxGeometry(10.5, 6.2, 28), steel, 0, 10.1, -6, g);
  add(new THREE.BoxGeometry(8, 3.4, 12), steel, 0, 14.6, -4, g);
  add(new THREE.CylinderGeometry(0.45, 0.55, 11, 8), steel, 0, 18, -8, g);
  add(new THREE.CylinderGeometry(0.9, 1.1, 4.2, 10), new THREE.MeshStandardMaterial({ color: 0x2a2a28 }), 1.4, 12.2, -18, g);
  add(new THREE.CylinderGeometry(4.4, 4.4, 0.2, 16), deckPaint, 0, 6.95, -52, g);
  const red = new THREE.MeshStandardMaterial({ color: 0xff2a22, emissive: 0xff2200, emissiveIntensity: 3.2 });
  const grn = new THREE.MeshStandardMaterial({ color: 0x33ff66, emissive: 0x22cc44, emissiveIntensity: 3.2 });
  add(new THREE.SphereGeometry(0.35, 10, 10), red, 7.2, 7.4, 48, g, false);
  add(new THREE.SphereGeometry(0.35, 10, 10), grn, -7.2, 7.4, 48, g, false);
  const deckL = new THREE.PointLight(0xffe1b0, 70, 80, 2);
  deckL.position.set(0, 14, -4);
  g.add(deckL);
  const bowL = new THREE.PointLight(0xffc978, 25, 40, 2);
  bowL.position.set(0, 8, 50);
  g.add(bowL);
  return g;
}
const ship = destroyer();
ship.position.set(-72, 0, -248);
ship.rotation.y = 0.32;
scene.add(ship);

const envelopes = new THREE.Group();
envelopes.visible = false;
function hemi(x, z, r, color) {
  const geo = new THREE.SphereGeometry(r, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2);
  const m = new THREE.Mesh(
    geo,
    new THREE.MeshPhysicalMaterial({
      color,
      transparent: true,
      opacity: 0.045,
      roughness: 0.12,
      metalness: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  m.position.set(x, 0.2, z);
  envelopes.add(m);
}
hemi(48, 118, 210, 0x7ec8c2);
hemi(168, 248, 320, 0x6fb4ae);
hemi(-72, -248, 140, 0xd8d2c4);
const cor = new THREE.Mesh(
  new THREE.BoxGeometry(200, 24, 400),
  new THREE.MeshBasicMaterial({ color: 0xc45c4a, transparent: true, opacity: 0.045, side: THREE.DoubleSide, depthWrite: false }),
);
cor.position.set(-60, 16, -220);
cor.add(new THREE.LineSegments(new THREE.EdgesGeometry(cor.geometry), new THREE.LineBasicMaterial({ color: 0xd56a56, transparent: true, opacity: 0.35 })));
envelopes.add(cor);
scene.add(envelopes);

function makeUAV(kind) {
  const g = new THREE.Group();
  const body = kind === "red"
    ? new THREE.MeshStandardMaterial({ color: 0x6a2a24, roughness: 0.45, metalness: 0.2 })
    : new THREE.MeshStandardMaterial({ color: 0x4a5a5c, roughness: 0.4, metalness: 0.35 });
  const fuse = add(new THREE.CylinderGeometry(0.28, 0.18, 4.6, 10), body, 0, 0, 0, g, false);
  fuse.rotation.x = Math.PI / 2;
  add(new THREE.ConeGeometry(0.28, 1.1, 10), body, 0, 0, 2.7, g, false).rotation.x = Math.PI / 2;
  add(new THREE.BoxGeometry(kind === "red" ? 6.4 : 8.2, 0.08, 1.05), body, 0, 0, 0.2, g, false);
  add(new THREE.BoxGeometry(0.08, 0.9, 0.7), body, 0, 0.45, -1.7, g, false);
  const col = kind === "red" ? 0xff3311 : 0x88e8ff;
  add(new THREE.SphereGeometry(0.12, 8, 8), new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 3.4 }), 0, 0.1, -2.1, g, false);
  g.userData.kind = kind;
  return g;
}

const blues = [];
function ringTeam(cx, cy, cz, n, rad, kind) {
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const u = makeUAV(kind);
    u.userData.home = { cx, cy, cz, rad, a0: a };
    scene.add(u);
    blues.push(u);
  }
}
ringTeam(130, 92, 190, 3, 28, "blue");
ringTeam(52, 46, 96, 3, 22, "blue");

const drones = [];
let k = 0;
let jammed = false;
let playing = false;

function spawnWave() {
  const live = drones.filter((d) => d.userData.alive).length;
  const room = N_MAX - live;
  if (k >= K_MAX || room < 3) {
    document.getElementById("caption").textContent =
      room < 3 ? `走廊已有 ${live} 架，N_max=${N_MAX}。` : `波次用尽 ${k}/${K_MAX}。`;
    return;
  }
  const n = Math.min(4, room);
  k += 1;
  for (let i = 0; i < n; i++) {
    const d = makeUAV("red");
    const ang = (i / n) * Math.PI * 2;
    d.position.set(-70 + 9 * Math.cos(ang), 16, -390 + 8 * Math.sin(ang));
    d.lookAt(d.position.x, 16, d.position.z + 20);
    d.userData.alive = true;
    scene.add(d);
    drones.push(d);
  }
  document.getElementById("v-k").textContent = `${k}/${K_MAX}`;
  document.getElementById("v-n").textContent = `${drones.filter((d) => d.userData.alive).length}/${N_MAX}`;
  document.getElementById("caption").textContent = `第 ${k} 波 N=${n}。贴海高度约 16 米，向码头飞。不锁人员。`;
}

function setJam(on) {
  jammed = on;
  links.forEach((l) => {
    l.material = on ? dimMat : goldMat;
  });
  document.getElementById("caption").textContent = on ? "通联切断。岸上灯还在，金线灭。" : "通联恢复。SAM / BMD / 指挥所重新交接。";
}

document.getElementById("play").addEventListener("click", () => {
  playing = !playing;
  document.getElementById("play").textContent = playing ? "止" : "演";
  if (playing && k === 0) spawnWave();
});
document.getElementById("wave").addEventListener("click", spawnWave);
document.getElementById("reset").addEventListener("click", () => {
  drones.forEach((d) => scene.remove(d));
  drones.length = 0;
  k = 0;
  playing = false;
  setJam(false);
  document.getElementById("play").textContent = "演";
  document.getElementById("v-k").textContent = `0/${K_MAX}`;
  document.getElementById("v-n").textContent = `0/${N_MAX}`;
  document.getElementById("caption").textContent = "复位。码头还在，海面还在反光。";
});
document.getElementById("jam").addEventListener("click", () => setJam(true));
document.getElementById("restore").addEventListener("click", () => setJam(false));
document.getElementById("env").addEventListener("click", () => {
  envelopes.visible = !envelopes.visible;
  document.getElementById("caption").textContent = envelopes.visible ? "包络打开：交战可行集，不是占领区。" : "包络关掉。只剩港区实体。";
});
document.getElementById("cam-port").addEventListener("click", () => look([-16, 4.1, -72], [24, 11, 28]));
document.getElementById("cam-sea").addEventListener("click", () => look([-55, 12, -355], [-40, 14, -160]));
document.getElementById("cam-orbit").addEventListener("click", () => look([-200, 95, -30], [30, 10, 70]));

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(vw0, vh0), 0.85, 0.5, 0.58);
composer.addPass(bloom);
composer.addPass(new OutputPass());

window.addEventListener("resize", () => {
  const [w, h] = viewSize();
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
  composer.setSize(w, h);
  bloom.setSize(w, h);
});

const clock = new THREE.Clock();
function tick() {
  const t = clock.getElapsedTime();
  const dt = clock.getDelta();
  water.material.uniforms.time.value = t * 0.35;
  radarVan.rotation.y = t * 0.35;
  ship.position.y = Math.sin(t * 0.7) * 0.35;
  ship.rotation.z = Math.sin(t * 0.55) * 0.012;
  blues.forEach((u, i) => {
    const h = u.userData.home;
    const a = h.a0 + t * 0.18;
    u.position.set(h.cx + h.rad * Math.cos(a), h.cy + Math.sin(t * 0.9 + i) * 1.2, h.cz + h.rad * Math.sin(a));
    u.lookAt(h.cx, h.cy, h.cz + 30);
  });
  if (playing) {
    drones.forEach((d) => {
      if (!d.userData.alive) return;
      d.position.z += 26 * dt;
      d.position.y = 16 + Math.sin(t * 1.6 + d.position.x) * 0.8;
      d.lookAt(d.position.x, d.position.y, d.position.z + 12);
      if (d.position.z > -36) d.position.z = -36;
    });
  }
  controls.update();
  composer.render();
  requestAnimationFrame(tick);
}
tick();
