// node --test tests-js/
import test from "node:test";
import assert from "node:assert/strict";
import { clamp, cropRect, dragPan, previewShift } from "../static/js/zoom.js";

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test("sem zoom, o recorte é o maior retângulo centrado com a proporção da saída", () => {
  // vídeo retrato 720x1280 para saída 1280x720: usa a largura toda
  const r = cropRect(720, 1280, 1280, 720, 1);
  near(r.sw, 720);
  near(r.sh, 720 * (720 / 1280));
  near(r.sx, 0);
  near(r.sy, (1280 - r.sh) / 2);
  // vídeo já 16:9: recorte é o quadro inteiro
  assert.deepEqual(cropRect(1280, 720, 1280, 720, 1), { sx: 0, sy: 0, sw: 1280, sh: 720 });
});

test("zoom divide o tamanho do recorte e mantém a proporção", () => {
  for (const z of [1.5, 2, 3, 4]) {
    const r = cropRect(1920, 1080, 1280, 720, z);
    near(r.sw, 1920 / z);
    near(r.sh, 1080 / z);
    near(r.sw / r.sh, 1280 / 720);
    near(r.sx + r.sw / 2, 960); // centrado
    near(r.sy + r.sh / 2, 540);
  }
});

test("pan -1..1 vai de uma borda à outra sem sair do vídeo", () => {
  for (const z of [1.2, 2, 4]) {
    const left = cropRect(1920, 1080, 1280, 720, z, -1, -1);
    const right = cropRect(1920, 1080, 1280, 720, z, 1, 1);
    near(left.sx, 0);
    near(left.sy, 0);
    near(right.sx + right.sw, 1920);
    near(right.sy + right.sh, 1080);
  }
  // pan fora da faixa é limitado
  assert.deepEqual(cropRect(1920, 1080, 1280, 720, 2, 9, -9), cropRect(1920, 1080, 1280, 720, 2, 1, -1));
});

test("em zoom 1 o pan não faz nada", () => {
  assert.deepEqual(cropRect(1920, 1080, 1280, 720, 1, 1, 1), cropRect(1920, 1080, 1280, 720, 1, 0, 0));
});

test("a prévia mostra a mesma janela que o PC recebe", () => {
  // centro da janela do recorte (fração do vídeo, em x) == -deslocamento da prévia
  for (const z of [1.5, 2, 3, 4]) {
    for (const pan of [-1, -0.5, 0, 0.3, 1]) {
      const r = cropRect(1920, 1080, 1280, 720, z, pan, 0);
      const centerOffset = (r.sx + r.sw / 2) / 1920 - 0.5;
      near(centerOffset * 100, -previewShift(z, pan), 1e-9);
    }
  }
  assert.equal(previewShift(1, 1), 0);
});

test("arrastar move a imagem junto com o dedo", () => {
  // dedo para a direita (dx>0): a janela vai para a esquerda, pan diminui (prévia normal)
  assert.ok(dragPan(100, 400, 2, false) < 0);
  assert.ok(dragPan(100, 400, 2, true) > 0); // espelhada: sentido oposto
  assert.equal(dragPan(100, 400, 1, false), 0);
  assert.equal(dragPan(100, 0, 3, false), 0);
  // arrastar a largura inteira em zoom 2 percorre todo o espaço livre (pan -2 => 100% do elemento)
  near(Math.abs(dragPan(400, 400, 2, false)), 2);
});

test("clamp", () => {
  assert.equal(clamp(5, 1, 4), 4);
  assert.equal(clamp(-1, 1, 4), 1);
  assert.equal(clamp(2, 1, 4), 2);
});
