import React, { useEffect, useRef } from "react";

interface MeshNode {
  bx: number;
  by: number;
  x: number;
  y: number;
  z: number;
  vz: number;
  nextZ: number;
  energy: number;
}

const CELL_SIZE = 56;
// 极缓慢、柔顺的耦合波参数：波浪传播舒缓沉静，无躁动抖动
const WAVE_STIFFNESS = 0.044;
const WAVE_RESTORING = 0.01;
const WAVE_DAMPING = 0.964;
const MOUSE_WAVE_RADIUS = 165;

/**
 * 登录页右侧极简黑白灰波浪粒子网展示区：
 * - 纯净高级灰 + 珠光浅白底色，无蓝色冲突；
 * - 全部节点相连成一张完整连续的银灰三角网面；
 * - 常态极缓慢静谧起伏，鼠标划过或点击时激起柔顺向外滚动的石墨灰波浪。
 */
export const LoginVisualPanel: React.FC = () => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

    let width = Math.max(1, container.clientWidth);
    let height = Math.max(1, container.clientHeight);
    let dpr = Math.min(window.devicePixelRatio || 1, 2);

    let cols = 0;
    let rows = 0;
    let nodes: MeshNode[] = [];

    const buildMesh = (w: number, h: number) => {
      const margin = CELL_SIZE * 1.25;
      cols = Math.max(6, Math.ceil((w + margin * 2) / CELL_SIZE) + 1);
      rows = Math.max(6, Math.ceil((h + margin * 2) / CELL_SIZE) + 1);
      const stepX = (w + margin * 2) / (cols - 1);
      const stepY = (h + margin * 2) / (rows - 1);

      let seed = 4729;
      const rand = () => {
        seed = (seed * 16807) % 2147483647;
        return (seed - 1) / 2147483646;
      };

      const nextNodes: MeshNode[] = [];
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const isBorder = r === 0 || r === rows - 1 || c === 0 || c === cols - 1;
          const jitterX = isBorder ? 0 : (rand() - 0.5) * stepX * 0.26;
          const jitterY = isBorder ? 0 : (rand() - 0.5) * stepY * 0.26;
          const bx = -margin + c * stepX + jitterX;
          const by = -margin + r * stepY + jitterY;
          nextNodes.push({
            bx,
            by,
            x: bx,
            y: by,
            z: 0,
            vz: 0,
            nextZ: 0,
            energy: 0,
          });
        }
      }
      nodes = nextNodes;
    };

    const syncCanvasSize = () => {
      width = Math.max(1, container.clientWidth);
      height = Math.max(1, container.clientHeight);
      dpr = Math.min(window.devicePixelRatio || 1, 2);

      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      buildMesh(width, height);
    };

    syncCanvasSize();

    const resizeObserver = new ResizeObserver(() => {
      syncCanvasSize();
    });
    resizeObserver.observe(container);

    const mouse = {
      x: width * 0.5,
      y: height * 0.5,
      prevX: width * 0.5,
      prevY: height * 0.5,
      speed: 0,
      inside: false,
    };

    const injectWaveImpulse = (cx: number, cy: number, radius: number, impulse: number) => {
      const r2 = radius * radius;
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const dx = n.bx - cx;
        const dy = n.by - cy;
        const d2 = dx * dx + dy * dy;
        if (d2 < r2) {
          const dist = Math.sqrt(d2);
          const falloff = Math.cos((dist / radius) * (Math.PI * 0.5));
          n.vz += impulse * falloff * falloff;
          n.energy = Math.min(1, n.energy + falloff * 0.36);
        }
      }
    };

    const handleMouseMove = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      const nx = e.clientX - rect.left;
      const ny = e.clientY - rect.top;
      if (!mouse.inside) {
        mouse.prevX = nx;
        mouse.prevY = ny;
      } else {
        mouse.prevX = mouse.x;
        mouse.prevY = mouse.y;
      }
      mouse.x = nx;
      mouse.y = ny;
      mouse.inside = true;
      const moveDist = Math.hypot(nx - mouse.prevX, ny - mouse.prevY);
      mouse.speed = Math.min(40, mouse.speed * 0.55 + moveDist * 0.8);

      if (!reducedMotion && moveDist > 1.2) {
        // 丝滑柔和的滚波脉冲，随鼠标轨迹向四周缓慢翻涌
        const strength = Math.min(3.4, 1.05 + moveDist * 0.085);
        injectWaveImpulse(nx, ny, MOUSE_WAVE_RADIUS, strength);
      }
    };

    const handleMouseLeave = () => {
      mouse.inside = false;
    };

    const handleClick = (e: MouseEvent) => {
      if (reducedMotion) return;
      const rect = container.getBoundingClientRect();
      injectWaveImpulse(
        e.clientX - rect.left,
        e.clientY - rect.top,
        MOUSE_WAVE_RADIUS * 1.4,
        7.2
      );
    };

    container.addEventListener("mousemove", handleMouseMove);
    container.addEventListener("mouseleave", handleMouseLeave);
    container.addEventListener("click", handleClick);

    let rafId = 0;
    let tick = 0;

    const render = () => {
      // 极缓慢的常态漂浮行波步进（宁静从容）
      tick += 0.0055;
      mouse.speed *= 0.9;

      ctx.clearRect(0, 0, width, height);

      // 1. 纯净建筑浅灰白底色（无冷蓝/荧光色偏）
      const bgGrad = ctx.createLinearGradient(0, 0, width, height);
      bgGrad.addColorStop(0, "#FAFAFA");
      bgGrad.addColorStop(0.55, "#F5F5F6");
      bgGrad.addColorStop(1, "#EFEFF1");
      ctx.fillStyle = bgGrad;
      ctx.fillRect(0, 0, width, height);

      const softLight = ctx.createRadialGradient(
        width * 0.5,
        height * 0.45,
        20,
        width * 0.5,
        height * 0.45,
        Math.max(width, height) * 0.65
      );
      softLight.addColorStop(0, "rgba(255, 255, 255, 0.72)");
      softLight.addColorStop(1, "rgba(244, 244, 245, 0)");
      ctx.fillStyle = softLight;
      ctx.fillRect(0, 0, width, height);

      // 2. 二维耦合波动更新（柔和长波阻尼，呈绸缎般波浪滚动）
      if (!reducedMotion) {
        if (mouse.inside && mouse.speed < 1.8) {
          injectWaveImpulse(
            mouse.x,
            mouse.y,
            MOUSE_WAVE_RADIUS * 0.72,
            Math.sin(tick * 5.0) * 0.14
          );
        }

        for (let r = 0; r < rows; r++) {
          for (let c = 0; c < cols; c++) {
            const idx = r * cols + c;
            const n = nodes[idx];

            let laplacian = 0;
            let count = 0;
            if (c > 0) {
              laplacian += nodes[idx - 1].z - n.z;
              count++;
            }
            if (c < cols - 1) {
              laplacian += nodes[idx + 1].z - n.z;
              count++;
            }
            if (r > 0) {
              laplacian += nodes[idx - cols].z - n.z;
              count++;
            }
            if (r < rows - 1) {
              laplacian += nodes[idx + cols].z - n.z;
              count++;
            }

            const avgDiff = count > 0 ? laplacian / count : 0;
            const acc = avgDiff * WAVE_STIFFNESS * 4 - n.z * WAVE_RESTORING;
            n.vz = (n.vz + acc) * WAVE_DAMPING;
            n.nextZ = Math.max(-36, Math.min(36, n.z + n.vz));
            n.energy = Math.max(0, n.energy * 0.968 - 0.0015);
          }
        }

        for (let i = 0; i < nodes.length; i++) {
          const n = nodes[i];
          n.z = n.nextZ;
          const waveEnergy = Math.min(1, Math.abs(n.z) / 16);
          if (waveEnergy > n.energy) {
            n.energy += (waveEnergy - n.energy) * 0.28;
          }
        }
      }

      // 3. 叠加极缓慢的长波长环境行波 + 交互波高
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const ambientWave = reducedMotion
          ? 0
          : Math.sin(n.bx * 0.0055 - tick * 1.1 + n.by * 0.0025) * 6.5 +
            Math.cos(n.by * 0.006 - tick * 0.85 - n.bx * 0.002) * 5.0;
        const totalH = ambientWave + n.z;
        n.x = n.bx + totalH * 0.34;
        n.y = n.by - totalH * 0.84;
      }

      // 4. 极淡的石墨灰波峰面片光影（勾勒立体波浪层次）
      for (let r = 0; r < rows - 1; r++) {
        for (let c = 0; c < cols - 1; c++) {
          const a = nodes[r * cols + c];
          const b = nodes[r * cols + c + 1];
          const d = nodes[(r + 1) * cols + c];
          const e = nodes[(r + 1) * cols + c + 1];

          const avgZ = (a.z + b.z + d.z + e.z) * 0.25;
          const avgEnergy = (a.energy + b.energy + d.energy + e.energy) * 0.25;
          const crest = Math.max(0, avgZ / 24);

          if (crest > 0.04 || avgEnergy > 0.08) {
            const fillAlpha = Math.min(0.055, crest * 0.036 + avgEnergy * 0.022);
            ctx.fillStyle = `rgba(24, 24, 27, ${fillAlpha.toFixed(3)})`;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.lineTo(e.x, e.y);
            ctx.lineTo(d.x, d.y);
            ctx.closePath();
            ctx.fill();
          }
        }
      }

      // 5. 绘制全连通银灰网格边（波峰平滑加深至石墨炭灰）
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const idx = r * cols + c;
          const p = nodes[idx];

          const drawEdge = (q: MeshNode, diag = false) => {
            const eEnergy = Math.max(p.energy, q.energy);
            const heightLift = Math.max(0, ((p.z + q.z) * 0.5) / 22);
            const baseAlpha = diag ? 0.1 : 0.16;
            const alpha = Math.min(0.48, baseAlpha + eEnergy * 0.24 + heightLift * 0.1);
            const tone = Math.round(132 - eEnergy * 96); // #84848B 银灰 -> #242427 深石墨
            ctx.strokeStyle = `rgba(${tone}, ${tone}, ${tone + 4}, ${alpha.toFixed(3)})`;
            ctx.lineWidth = (diag ? 0.65 : 0.85) + eEnergy * 0.55;
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
            ctx.stroke();
          };

          if (c < cols - 1) drawEdge(nodes[idx + 1], false);
          if (r < rows - 1) drawEdge(nodes[idx + cols], false);
          if (r < rows - 1 && c < cols - 1) {
            if ((r + c) % 2 === 0) {
              drawEdge(nodes[idx + cols + 1], true);
            } else {
              const right = nodes[idx + 1];
              const bottom = nodes[idx + cols];
              const eEnergy = Math.max(right.energy, bottom.energy);
              const alpha = Math.min(0.42, 0.1 + eEnergy * 0.22);
              const tone = Math.round(132 - eEnergy * 96);
              ctx.strokeStyle = `rgba(${tone}, ${tone}, ${tone + 4}, ${alpha.toFixed(3)})`;
              ctx.lineWidth = 0.65 + eEnergy * 0.45;
              ctx.beginPath();
              ctx.moveTo(right.x, right.y);
              ctx.lineTo(bottom.x, bottom.y);
              ctx.stroke();
            }
          }
        }
      }

      // 6. 绘制高级灰网面节点
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const lift = Math.max(0, n.z / 22);
        const radius = 1.75 + n.energy * 1.35 + lift * 0.5;
        const alpha = Math.min(0.86, 0.34 + n.energy * 0.44 + lift * 0.12);

        if (n.energy > 0.24) {
          ctx.fillStyle = `rgba(39, 39, 42, ${(n.energy * 0.11).toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(n.x, n.y, radius * 2.4, 0, Math.PI * 2);
          ctx.fill();
        }

        const tone = Math.round(116 - n.energy * 88); // 银灰 -> 曜石黑
        ctx.fillStyle = `rgba(${tone}, ${tone}, ${tone + 3}, ${alpha.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(n.x, n.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }

      if (!reducedMotion) {
        rafId = window.requestAnimationFrame(render);
      }
    };

    render();

    return () => {
      window.cancelAnimationFrame(rafId);
      resizeObserver.disconnect();
      container.removeEventListener("mousemove", handleMouseMove);
      container.removeEventListener("mouseleave", handleMouseLeave);
      container.removeEventListener("click", handleClick);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="relative hidden lg:flex lg:col-span-7 overflow-hidden bg-[#FAFAFA] select-none"
    >
      <canvas ref={canvasRef} className="absolute inset-0 w-full h-full block" />

      {/* 极简单行墨黑排版：仅保留一句精炼中文标识，留白克制、无英文重复与装饰线条 */}
      <div className="relative z-10 w-full h-full flex items-center justify-center px-12 -translate-y-3 pointer-events-none">
        <div
          className="px-14 py-8 text-center"
          style={{
            background:
              "radial-gradient(ellipse at center, rgba(250,250,250,0.86) 0%, rgba(250,250,250,0.48) 50%, rgba(250,250,250,0) 76%)",
          }}
        >
          <h2 className="text-[30px] xl:text-[34px] font-medium text-[#18181B] tracking-[0.22em] pl-[0.22em] leading-none">
            云服务智能分诊
          </h2>
        </div>
      </div>
    </div>
  );
};
