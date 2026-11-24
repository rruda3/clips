import {
  activeWordIndex,
  captionGroups,
  captionAt,
  captionTransform,
  frameRect,
  hookRange,
  isSourceSpanActive,
  mappedWords,
  outputSize,
  replaceWords,
  editDuration,
  textTransform,
  type TextLayer,
  type CaptionLook,
  type CaptionTransform,
  type CaptionWord,
  type EditDocument,
} from "./document";

const groupCache = new WeakMap<
  EditDocument,
  ReturnType<typeof captionGroups>
>();
const lookCache = new WeakMap<
  EditDocument,
  { looks?: Record<string, CaptionLook>; look: ReturnType<typeof resolveLook> }
>();
const loaded = new Map<string, Promise<void>>();
export function loadEditorFont(name: string) {
  if (!loaded.has(name))
    loaded.set(
      name,
      (async () => {
        const font = new FontFace(
          `Editor-${name}`,
          `url(/api/fonts/${encodeURIComponent(name)})`,
        );
        await font.load();
        document.fonts.add(font);
      })().catch((error) => {
        loaded.delete(name);
        throw error;
      }),
    );
  return loaded.get(name)!;
}

// Used until the editor state delivers the templates (and for drafts without one).
const FALLBACK_LOOK: CaptionLook = {
  animation: "karaoke",
  wordPop: true,
  wordBox: false,
  wordBoxColor: null,
  highlightColor: null,
  emphasisColor: null,
  strokeColor: "#000000",
  strokeWidth: 3,
  shadow: true,
  glow: false,
  backgroundColor: null,
};
const HOOK_SIDE_MARGIN = 0.07, HOOK_TOP_MARGIN = 0.12;

/** libass sizes fonts by ascender + descender, not the em box CSS uses. */
function setAssFont(ctx: CanvasRenderingContext2D, font: string, size: number) {
  ctx.font = `${size}px "Editor-${font}", sans-serif`;
  const metrics = ctx.measureText("A"),
    height = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent;
  const pixels = height > 0 ? size * size / height : size;
  ctx.font = `${pixels}px "Editor-${font}", sans-serif`;
  return pixels;
}

export interface TextDraft { hookText?: string; captionWords?: CaptionWord[] }

function wrapCaptionWords(ctx: CanvasRenderingContext2D, words: CaptionWord[], uppercase: boolean | undefined, width: number) {
  const lines: CaptionWord[][] = [];
  const space = ctx.measureText(" ").width;
  let line: CaptionWord[] = [], length = 0;
  for (const word of words) {
    const w = ctx.measureText(uppercase ? word.text.toUpperCase() : word.text).width;
    if (line.length && length + space + w > width) { lines.push(line); line = []; length = 0; }
    length += (line.length ? space : 0) + w;
    line.push(word);
  }
  if (line.length) lines.push(line);
  return lines;
}

/** The native field supplies caret/selection; these are the same font metrics as the canvas. */
export function textEditorLayout(ctx: CanvasRenderingContext2D, doc: EditDocument, width: number, height: number, time: number, layer: TextLayer, draft?: TextDraft) {
  const group = captionAt(doc, time) ?? [];
  const transform: CaptionTransform = group.length ? captionTransform(doc, group) : textTransform(doc, "captions");
  const hook = hookTitleLayout(draft?.hookText === undefined ? doc : { ...doc, hookTitle: draft.hookText }, width, height);
  const size = layer === "hook" ? hook.px : Math.max(1, Math.round((transform.size ?? doc.captions.size) * width * transform.scale / 1080));
  const fontSize = setAssFont(ctx, doc.captions.font, size);
  const wrapWidth = Math.max(1, layer === "hook" ? hook.usable : width - 120);
  const lines = layer === "hook" ? hook.lines : wrapCaptionWords(ctx, draft?.captionWords ?? group, doc.captions.uppercase, wrapWidth).map(line => line.map(word => word.text).join(" "));
  return { fontSize, wrapWidth, lineHeight: size * 1.2, lines };
}

export function prepareTextDraft(doc: EditDocument, time: number, layer: TextLayer, text: string): TextDraft {
  if (layer === "captions") return { captionWords: replaceWords(captionAt(doc, time) ?? [], text, editDuration(doc)) };
  return { hookText: text.trim().replace(/\s+/g, " ") };
}
export type TextEditorLayout = ReturnType<typeof textEditorLayout>;

export function formatHookEditorText(doc: EditDocument, width: number, height: number, text: string) {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return text;
  const layout = hookTitleLayout({ ...doc, hookTitle: words.join(" ") }, width, height);
  let cursor = 0;
  return layout.lines.map(line => { const count = line.trim().split(/\s+/).length; const value = words.slice(cursor, cursor + count).join(" "); cursor += count; return value; }).join("\n") + (/\s$/.test(text) ? " " : "");
}

/** Linear keyframes, as ASS `\t` interpolates them. */
function keyframes(t: number, points: [number, number][]) {
  if (t <= points[0][0]) return points[0][1];
  for (let i = 1; i < points.length; i++) {
    const [t1, v1] = points[i],
      [t0, v0] = points[i - 1];
    if (t <= t1) return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
  }
  return points[points.length - 1][1];
}

/** Mirrors `editor_caption_template` and the generation renderer's pixel maths. */
function resolveLook(doc: EditDocument, looks?: Record<string, CaptionLook>) {
  const style = doc.captions,
    look = looks?.[style.template ?? "default"] ?? FALLBACK_LOOK;
  const animation = style.karaoke
    ? "karaoke"
    : look.animation === "karaoke"
      ? "none"
      : look.animation;
  const tiedEmphasis =
    (look.emphasisColor ?? look.highlightColor) === look.highlightColor;
  // The background toggle decides whether the template's box colour applies.
  const backgroundColor = style.background
    ? (look.backgroundColor ?? "#00000080")
    : null;
  return {
    ...look,
    animation,
    backgroundColor,
    emphasisColor: tiedEmphasis
      ? style.accent
      : (look.emphasisColor ?? style.accent),
    outlineColor: look.strokeColor ?? "#000000",
    backColor: backgroundColor ?? "#00000080",
  };
}
function cachedLook(doc: EditDocument, looks?: Record<string, CaptionLook>) {
  const cached = lookCache.get(doc);
  if (cached && cached.looks === looks) return cached.look;
  const look = resolveLook(doc, looks);
  lookCache.set(doc, { looks, look });
  return look;
}

/** Stock footage drawn over the whole frame, under the captions. */
export interface BRollLayer {
  width: number;
  height: number;
  alpha: number;
  draw: (x: number, y: number, width: number, height: number) => void;
}

export interface SourceCropRect {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
}

function coverCropForBox(
  sx: number,
  sy: number,
  sw: number,
  sh: number,
  dw: number,
  dh: number,
): SourceCropRect {
  const srcAspect = sw / Math.max(1, sh);
  const dstAspect = dw / Math.max(1, dh);
  if (srcAspect > dstAspect) {
    const cropW = sh * dstAspect;
    return { sx: sx + (sw - cropW) / 2, sy, sw: cropW, sh };
  }
  const cropH = sw / dstAspect;
  return { sx, sy: sy + (sh - cropH) / 2, sw, sh: cropH };
}

export function resolveWebcamCropRect(
  wb: EditDocument["viral"] extends infer V
    ? V extends { webcamBox: infer W }
      ? W | null | undefined
      : never
    : never,
  sourceWidth: number,
  sourceHeight: number,
): { x: number; y: number; w: number; h: number } {
  if (!wb) {
    return {
      x: 0,
      y: 0,
      w: Math.max(4, sourceWidth * 0.28),
      h: Math.max(4, sourceHeight * 0.34),
    };
  }
  const camZoom = Math.max(0.5, Math.min(2.5, wb.zoom ?? 1));
  const baseW = Math.max(4, Math.min(sourceWidth, wb.w * sourceWidth));
  const baseH = Math.max(4, Math.min(sourceHeight, wb.h * sourceHeight));
  const rawX = Math.max(0, Math.min(sourceWidth - baseW, wb.x * sourceWidth));
  const rawY = Math.max(0, Math.min(sourceHeight - baseH, wb.y * sourceHeight));
  const midX = rawX + baseW / 2;
  const midY = rawY + baseH / 2;
  const camW = Math.max(4, Math.min(sourceWidth, baseW / camZoom));
  const camH = Math.max(4, Math.min(sourceHeight, baseH / camZoom));
  const camX = Math.max(0, Math.min(sourceWidth - camW, midX - camW / 2));
  const camY = Math.max(0, Math.min(sourceHeight - camH, midY - camH / 2));
  return { x: camX, y: camY, w: camW, h: camH };
}

export function drawEditorFrame(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  sourceWidth: number,
  sourceHeight: number,
  time: number,
  draw: (rect: ReturnType<typeof frameRect>, srcCrop?: SourceCropRect) => void,
  looks?: Record<string, CaptionLook>,
  broll?: BRollLayer | null,
  textLayers: "all" | "none" = "all",
) {
  const { width, height } = ctx.canvas,
    fx = doc.effects,
    viral = doc.viral;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "black";
  ctx.fillRect(0, 0, width, height);
  ctx.save();

  let isGrayscale = false;
  let inFreezeBw = false;
  let dynamicZoom = 1;
  let shakeX = 0;
  let shakeY = 0;

  if (viral) {
    for (const te of viral.timeEffects ?? []) {
      if (te.kind === "freeze_bw" && isSourceSpanActive(doc, te.start, te.end, time)) {
        isGrayscale = true;
        inFreezeBw = true;
        dynamicZoom = Math.max(dynamicZoom, 1.12);
      }
    }
    for (const kf of viral.cameraKeyframes ?? []) {
      if (isSourceSpanActive(doc, kf.start, kf.end, time)) {
        if (kf.mode === "bw_dramatic") isGrayscale = true;
        else if (kf.mode === "punch") dynamicZoom = Math.max(dynamicZoom, kf.zoom || 1.22);
        else if (kf.mode === "slow_tension") {
          const spanLen = Math.max(0.1, kf.end - kf.start);
          const progress = Math.min(1, Math.max(0, ((time - kf.start) % spanLen + spanLen) / spanLen));
          dynamicZoom = Math.max(dynamicZoom, 1 + ((kf.zoom || 1.2) - 1) * progress);
        } else if (kf.mode === "shake") {
          dynamicZoom = Math.max(dynamicZoom, kf.zoom || 1.15);
          const amp = (kf.intensity || 0.6) * (width / 1080) * 18;
          shakeX = Math.sin(time * 75) * amp;
          shakeY = Math.cos(time * 65) * amp;
        }
      }
    }
  }

  const sat = isGrayscale ? 0 : fx.saturation;
  const con = isGrayscale ? Math.min(180, fx.contrast * 1.22) : fx.contrast;
  ctx.filter = `brightness(${fx.brightness}%) contrast(${con}%) saturate(${sat}%) blur(${(fx.blur * width) / 1080}px) hue-rotate(${fx.hue}deg)`;

  if (dynamicZoom > 1.001 || shakeX !== 0 || shakeY !== 0) {
    ctx.translate(width / 2 + shakeX, height / 2 + shakeY);
    ctx.scale(dynamicZoom, dynamicZoom);
    ctx.translate(-width / 2, -height / 2);
  }

  const { x: camX, y: camY, w: camW, h: camH } = resolveWebcamCropRect(
    viral?.webcamBox,
    sourceWidth,
    sourceHeight,
  );
  const canLayoutLandscape = Boolean(viral && sourceWidth > sourceHeight);

  if (viral?.layoutMode === "split" && canLayoutLandscape) {
    if (width <= height) {
      const topH = Math.round(height * (width === height ? 0.38 : 1 / 3));
      const botH = height - topH;
      draw(
        { x: 0, y: 0, width, height: topH },
        coverCropForBox(camX, camY, camW, camH, width, topH),
      );
      draw(
        { x: 0, y: topH, width, height: botH },
        coverCropForBox(0, 0, sourceWidth, sourceHeight, width, botH),
      );
      ctx.save();
      ctx.filter = "none";
      ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
      ctx.fillRect(0, topH - 2, width, 4);
      ctx.restore();
    } else {
      const leftW = Math.round(width * 0.34);
      const rightW = width - leftW;
      draw(
        { x: 0, y: 0, width: leftW, height },
        coverCropForBox(camX, camY, camW, camH, leftW, height),
      );
      draw(
        { x: leftW, y: 0, width: rightW, height },
        coverCropForBox(0, 0, sourceWidth, sourceHeight, rightW, height),
      );
      ctx.save();
      ctx.filter = "none";
      ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
      ctx.fillRect(leftW - 2, 0, 4, height);
      ctx.restore();
    }
  } else if (viral?.layoutMode === "fullscreen_cam" && canLayoutLandscape) {
    draw(
      { x: 0, y: 0, width, height },
      coverCropForBox(camX, camY, camW, camH, width, height),
    );
  } else if (viral?.layoutMode === "pip" && canLayoutLandscape) {
    draw(frameRect(doc.framing, sourceWidth, sourceHeight, width, height));
    const pipW = Math.max(80, Math.round(width * 0.30));
    const pipH = Math.max(60, Math.round(pipW * 0.78));
    const pipX = Math.max(12, Math.round(width * 0.04));
    const pipY = Math.max(12, height - pipH - Math.round(height * 0.11));
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.fillRect(pipX - 3, pipY - 3, pipW + 6, pipH + 6);
    draw(
      { x: pipX, y: pipY, width: pipW, height: pipH },
      coverCropForBox(camX, camY, camW, camH, pipW, pipH),
    );
    ctx.restore();
  } else {
    draw(frameRect(doc.framing, sourceWidth, sourceHeight, width, height));
  }

  if (inFreezeBw) {
    ctx.save();
    ctx.filter = "none";
    const grad = ctx.createRadialGradient(
      width / 2,
      height / 2,
      width * 0.25,
      width / 2,
      height / 2,
      height * 0.7,
    );
    grad.addColorStop(0, "rgba(0,0,0,0)");
    grad.addColorStop(1, "rgba(0,0,0,0.45)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, width, height);
    ctx.restore();
  }

  ctx.restore();
  if (broll && broll.alpha > 0 && broll.width > 0 && broll.height > 0) {
    // Fill the frame (cover), centred, as the export's scale+crop does.
    const scale = Math.max(width / broll.width, height / broll.height),
      w = broll.width * scale,
      h = broll.height * scale;
    ctx.save();
    ctx.globalAlpha = broll.alpha;
    broll.draw((width - w) / 2, (height - h) / 2, w, h);
    ctx.restore();
  }
  if (textLayers === "all") drawEditorTextLayer(ctx, doc, sourceWidth, sourceHeight, time, looks, "all", false);
}

/** Transparent editable layers in the viewer; composited only when exporting. */
export function drawEditorTextLayer(
  ctx: CanvasRenderingContext2D, doc: EditDocument, sourceWidth: number, sourceHeight: number,
  time: number, looks: Record<string, CaptionLook> | undefined, layer: "hook" | "captions" | "all", clear = true, draft?: TextDraft,
) {
  const { width, height } = ctx.canvas;
  if (clear) ctx.clearRect(0, 0, width, height);
  const out = outputSize(doc, sourceWidth, sourceHeight), look = cachedLook(doc, looks);
  ctx.save();
  ctx.scale(width / out.width, height / out.height);
  if (layer !== "hook" && doc.captions.enabled) drawCaptions(ctx, doc, out, time, look, draft?.captionWords);
  const hookDocument = draft?.hookText === undefined ? doc : { ...doc, hookTitle: draft.hookText };
  if (layer !== "captions" && hookDocument.hookTitle?.trim()) drawHookTitle(ctx, hookDocument, out, time, look);
  if ((layer === "hook" || layer === "all") && doc.viral?.subtexts?.length) drawViralSubtexts(ctx, doc, out, time);
  ctx.restore();
}

function drawViralSubtexts(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  { width, height }: { width: number; height: number },
  time: number,
) {
  const subtexts = doc.viral?.subtexts ?? [];
  for (const sub of subtexts) {
    if (!isSourceSpanActive(doc, sub.start, sub.end, time)) continue;
    const x = sub.x * width;
    const y = sub.y * height;
    ctx.save();
    const fontPx = Math.max(28, Math.round((46 * width) / 1080));
    ctx.font = `${sub.variant === "thought" ? "italic " : ""}800 ${fontPx}px "Editor-${doc.captions.font}", sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const metrics = ctx.measureText(sub.text);
    const padX = 24;
    const padY = 14;
    if (sub.variant === "censor_bar") {
      ctx.fillStyle = "#000000";
      ctx.fillRect(x - metrics.width / 2 - padX, y - fontPx / 2 - padY, metrics.width + padX * 2, fontPx + padY * 2);
      ctx.fillStyle = "#FF4D4D";
      ctx.fillText(sub.text, x, y);
    } else {
      ctx.lineWidth = 8;
      ctx.strokeStyle = "#000000";
      ctx.strokeText(sub.text, x, y);
      ctx.fillStyle = sub.color || "#FACC15";
      ctx.fillText(sub.text, x, y);
    }
    ctx.restore();
  }
}

function drawCaptions(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  { width, height }: { width: number; height: number },
  time: number,
  look: ReturnType<typeof resolveLook>,
  draftWords?: CaptionWord[],
) {
  let groups = groupCache.get(doc);
  if (!groups) {
    groups = captionGroups(mappedWords(doc), doc.captions.wordsPerLine);
    groupCache.set(doc, groups);
  }
  const original = groups.find(
    (group) => time >= group[0].start && time < group[group.length - 1].end,
  );
  const group = draftWords ?? original;
  if (!original || !group?.length) return;
  const transform = captionTransform(doc, original);
  const style = doc.captions,
    size = Math.max(1, Math.round(((transform.size ?? style.size) * width * transform.scale) / 1080)),
    karaoke = look.animation === "karaoke",
    active = karaoke ? group[activeWordIndex(group, time)] : undefined,
    label = (text: string) => (style.uppercase ? text.toUpperCase() : text);
  const outline =
    look.strokeColor && look.strokeWidth
      ? Math.max(
          look.strokeWidth,
          Math.round((size * look.strokeWidth) / 26),
        )
      : 0,
    shadow = look.shadow ? Math.max(2, Math.floor(size / 20)) : 0,
    boxPadding = Math.max(outline + 2, Math.floor(size / 5)),
    linePadding = Math.max(outline, Math.floor(size / 6)),
    centre = Math.round(height * (style.y + transform.y));
  const t = time - group[0].start,
    remaining = group[group.length - 1].end - time;
  let scale = 1,
    alpha = 1;
  if (karaoke && look.wordPop) scale = keyframes(t, [[0, 0.92], [0.14, 1]]);
  else if (look.animation === "pop")
    scale = keyframes(t, [[0, 0.88], [0.13, 1.06], [0.25, 1]]);
  else if (look.animation === "bounce")
    scale = keyframes(t, [[0, 0.7], [0.12, 1.12], [0.24, 1]]);
  else if (look.animation === "fade")
    alpha = Math.max(0, Math.min(1, t / 0.12, remaining / 0.12));

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(width * (0.5 + transform.x), centre);
  ctx.scale(scale, scale);
  setAssFont(ctx, style.font, size);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  // Fit each phrase in the safe width, using the same font for preview and browser export.
  const space = ctx.measureText(" ").width;
  const lines = wrapCaptionWords(ctx, group, style.uppercase, width - 120);
  lines.forEach((words, index) => {
    const total =
      words.reduce((sum, w) => sum + ctx.measureText(label(w.text)).width, 0) +
      space * (words.length - 1);
    const y = (index - (lines.length - 1) / 2) * size * 1.2;
    if (look.backgroundColor) {
      ctx.fillStyle = look.backgroundColor;
      ctx.fillRect(
        -total / 2 - linePadding,
        y - size / 2 - linePadding,
        total + linePadding * 2,
        size + linePadding * 2,
      );
    }
    let x = -total / 2;
    for (const word of words) {
      const text = label(word.text),
        textWidth = ctx.measureText(text).width,
        isActive = word === active;
      if (isActive && look.wordBox) {
        ctx.fillStyle = look.wordBoxColor ?? style.accent;
        ctx.beginPath();
        ctx.roundRect(
          x - boxPadding,
          y - size * 0.45 - boxPadding,
          textWidth + boxPadding * 2,
          size * 0.9 + boxPadding * 2,
          boxPadding,
        );
        ctx.fill();
      } else if (!look.backgroundColor && (outline || shadow)) {
        ctx.save();
        if (shadow) {
          ctx.shadowColor = look.backColor;
          ctx.shadowOffsetX = ctx.shadowOffsetY = shadow;
        }
        if (look.glow) ctx.filter = "blur(4px)";
        ctx.strokeStyle = look.outlineColor;
        ctx.lineWidth = outline * 2;
        if (outline) ctx.strokeText(text, x, y);
        else {
          ctx.fillStyle = look.backColor;
          ctx.fillText(text, x, y);
        }
        ctx.restore();
      }
      ctx.fillStyle = isActive
        ? style.accent
        : word.highlight
          ? look.emphasisColor
          : style.color;
      ctx.fillText(text, x, y);
      x += textWidth + space;
    }
  });
  ctx.restore();
}

/** Split the title into one line, or two lines balanced around the middle. */
function balanceTitle(words: string[], maxChars: number) {
  const text = words.join(" ");
  if (text.length <= maxChars || words.length < 2) return [text];
  let best = [text],
    longest = text.length;
  for (let i = 1; i < words.length; i++) {
    const lines = [words.slice(0, i).join(" "), words.slice(i).join(" ")];
    const candidate = Math.max(lines[0].length, lines[1].length);
    if (candidate < longest) {
      longest = candidate;
      best = lines;
    }
  }
  return best;
}

export function hookTitleLayout(doc: EditDocument, width: number, height: number) {
  const title = doc.hookTitle?.trim() ?? "";
  const text = doc.captions.uppercase ? title.toUpperCase() : title;
  const captionPx = Math.max(1, Math.round((doc.captions.size * width) / 1080));
  const basePx = Math.max(34, Math.min(66, Math.floor(captionPx * 0.82)));
  const usable = width - 2 * Math.max(48, Math.floor(width * HOOK_SIDE_MARGIN));
  const maxChars = Math.max(10, Math.floor(usable / (basePx * 0.52)));
  const lines = balanceTitle(text.split(/\s+/), maxChars);
  const longest = Math.max(...lines.map(line => line.length));
  const originalPx = longest > maxChars ? Math.max(30, Math.min(basePx, Math.floor(usable / (longest * 0.52)))) : basePx;
  const transform = textTransform(doc, "hook");
  const px = originalPx * transform.scale;
  const top = Math.max(48, Math.floor(height * HOOK_TOP_MARGIN)) + transform.y * height + (originalPx - px) * lines.length * 0.6;
  return { lines, px, usable, top, x: width * (0.5 + transform.x) };
}

/** The generated clip's headline: top safe area, fading in and out over ~4s. */
function drawHookTitle(
  ctx: CanvasRenderingContext2D,
  doc: EditDocument,
  { width, height }: { width: number; height: number },
  time: number,
  look: ReturnType<typeof resolveLook>,
) {
  const { start, end } = hookRange(doc);
  if (time < start || time >= end) return;
  const style = doc.captions;
  const { lines, px, top, x: centreX } = hookTitleLayout(doc, width, height);
  const hasOutline = !!look.strokeColor && look.strokeWidth > 0,
    // Mirrors build_hook_title_ass: an opaque box only without an outline.
    boxed = !hasOutline && !!look.backgroundColor,
    outline = boxed
      ? Math.max(4, Math.floor(px / 6))
      : hasOutline
        ? Math.max(look.strokeWidth, Math.round((px * look.strokeWidth) / 26))
        : Math.max(2, Math.floor(px / 16)),
    shadow = look.shadow ? Math.max(2, Math.floor(px / 20)) : 0;
  const t = time - start;
  ctx.save();
  const fadeIn = Math.min(.16, (end - start) / 3), fadeOut = Math.min(.24, (end - start) / 3);
  ctx.globalAlpha = Math.max(0, Math.min(1, t / fadeIn, (end - time) / fadeOut));
  const lineHeight = px * 1.2,
    scale = look.wordPop ? keyframes(t, [[0, 0.9], [fadeIn, 1]]) : 1;
  ctx.translate(centreX, top + (lineHeight * lines.length) / 2);
  ctx.scale(scale, scale);
  setAssFont(ctx, style.font, px);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  const space = ctx.measureText(" ").width;
  lines.forEach((line, index) => {
    const y = (index - (lines.length - 1) / 2) * lineHeight,
      words = line.split(" "),
      total =
        words.reduce((sum, w) => sum + ctx.measureText(w).width, 0) +
        space * (words.length - 1);
    if (boxed) {
      ctx.fillStyle = look.outlineColor;
      ctx.fillRect(
        -total / 2 - outline,
        y - lineHeight / 2 - outline / 2,
        total + outline * 2,
        lineHeight + outline,
      );
    }
    let x = -total / 2;
    for (const word of words) {
      if (!boxed) {
        ctx.save();
        if (shadow) {
          ctx.shadowColor = look.backColor;
          ctx.shadowOffsetX = ctx.shadowOffsetY = shadow;
        }
        ctx.strokeStyle = look.outlineColor;
        ctx.lineWidth = outline * 2;
        ctx.strokeText(word, x, y);
        ctx.restore();
      }
      // Numbers take the accent; the export also accents power words.
      ctx.fillStyle = /\d/.test(word) ? look.emphasisColor : style.color;
      ctx.fillText(word, x, y);
      x += ctx.measureText(word).width + space;
    }
  });
  ctx.restore();
}

export interface TextBounds { x: number; y: number; width: number; height: number }

/** Selection geometry in output pixels, measured with the renderer's font. */
export function textLayerBounds(ctx: CanvasRenderingContext2D, doc: EditDocument, width: number, height: number, time: number, layer: TextLayer, looks?: Record<string, CaptionLook>, draft?: TextDraft): TextBounds | null {
  const look = cachedLook(doc, looks);
  if (layer === "hook") {
    if (!doc.hookTitle?.trim()) return null;
    const { lines, px, top, x } = hookTitleLayout(draft?.hookText === undefined ? doc : { ...doc, hookTitle: draft.hookText }, width, height);
    setAssFont(ctx, doc.captions.font, px);
    const pad = Math.max(5, Math.round(px * (look.strokeWidth || 2) / 26)) + 3;
    const w = Math.max(...lines.map(line => ctx.measureText(line).width)) + pad * 2;
    return { x: x - w / 2, y: top - pad, width: w, height: lines.length * px * 1.2 + pad * 2 };
  }
  const original = captionAt(doc, time);
  const group = draft?.captionWords ?? original;
  if (!original || !group?.length || !doc.captions.enabled) return null;
  const transform = captionTransform(doc, original);
  const px = Math.max(1, Math.round((transform.size ?? doc.captions.size) * width * transform.scale / 1080));
  setAssFont(ctx, doc.captions.font, px);
  const space = ctx.measureText(" ").width;
  const lines = wrapCaptionWords(ctx, group, doc.captions.uppercase, width - 120);
  const longest = Math.max(...lines.map(line => line.reduce((sum, word) => sum + ctx.measureText(doc.captions.uppercase ? word.text.toUpperCase() : word.text).width, 0) + space * (line.length - 1)));
  const pad = Math.max(px / 5, look.strokeWidth * px / 26, 5) + 3;
  const w = longest + pad * 2, h = lines.length * px * 1.2 + pad * 2;
  return { x: width * (0.5 + transform.x) - w / 2, y: height * (doc.captions.y + transform.y) - h / 2, width: w, height: h };
}
