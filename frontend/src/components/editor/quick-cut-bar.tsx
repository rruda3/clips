"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import {
  ArrowLeftToLine,
  ArrowRightToLine,
  ExternalLink,
  Loader2,
  MapPin,
  RotateCcw,
  Scissors,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  clamp,
  cutOutputRange,
  deleteSegmentAt,
  editDuration,
  locateOutputTime,
  outputTime,
  splitSegment,
  timecode,
  trimEndAt,
  trimStartAt,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import { cn } from "@/lib/utils";

interface QuickCutBarProps {
  doc: EditDocument;
  asset: EditorState;
  time: number;
  segmentIndex: number;
  onSeek: (segmentIndex: number, sourceTime: number) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  onOpenFullEditor?: () => Promise<void> | void;
  openingFullEditor?: boolean;
}

export function QuickCutBar({
  doc,
  asset,
  time,
  segmentIndex,
  onSeek,
  update,
  onOpenFullEditor,
  openingFullEditor = false,
}: QuickCutBarProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [cutMarkA, setCutMarkA] = useState<number | null>(null);

  const totalDuration = Math.max(0.01, editDuration(doc));
  const safeSegIndex = clamp(segmentIndex, 0, Math.max(0, doc.segments.length - 1));
  const currentOut = clamp(outputTime(doc, safeSegIndex, time), 0, totalDuration);

  const isTrimmedOrSplit =
    doc.segments.length > 1 ||
    (doc.segments[0] &&
      (doc.segments[0].start > 0.05 ||
        Math.abs(doc.segments[0].end - asset.duration) > 0.1));

  // Build proportional segment layout + sampled waveform bars for each kept segment
  const segmentLayouts = useMemo(() => {
    const wave = asset.waveform ?? [];
    const srcDur = Math.max(0.01, asset.duration);
    let cursor = 0;
    return doc.segments.map((seg, idx) => {
      const len = Math.max(0, seg.end - seg.start);
      const startOut = cursor;
      const endOut = cursor + len;
      cursor = endOut;
      const barsCount = Math.max(6, Math.round((len / totalDuration) * 72));
      const bars: number[] = [];
      for (let b = 0; b < barsCount; b++) {
        const srcT = seg.start + ((b + 0.5) / barsCount) * len;
        const wIdx = Math.min(
          wave.length - 1,
          Math.max(0, Math.floor((srcT / srcDur) * wave.length)),
        );
        bars.push(wave.length ? clamp(wave[wIdx] ?? 0.2, 0.08, 1) : 0.35);
      }
      return {
        seg,
        idx,
        len,
        startOut,
        endOut,
        widthPct: (len / totalDuration) * 100,
        bars,
      };
    });
  }, [asset.duration, asset.waveform, doc.segments, totalDuration]);

  const seekFromClientX = useCallback(
    (clientX: number) => {
      const el = trackRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const frac = clamp((clientX - rect.left) / Math.max(1, rect.width), 0, 1);
      const targetOut = frac * totalDuration;
      const loc = locateOutputTime(doc, targetOut);
      onSeek(loc.segmentIndex, loc.sourceTime);
    },
    [doc, onSeek, totalDuration],
  );

  const handleSplitHere = useCallback(() => {
    const seg = doc.segments[safeSegIndex];
    if (!seg) return;
    if (time - seg.start < 0.08 || seg.end - time < 0.08) {
      toast.info("Mova a agulha para o meio do trecho para dividir.");
      return;
    }
    update((current) => splitSegment(current, safeSegIndex, time), "quickcut:split");
    setCutMarkA(null);
    toast.success(`Vídeo dividido em ${timecode(currentOut)}!`);
  }, [currentOut, doc.segments, safeSegIndex, time, update]);

  const handleTrimStart = useCallback(() => {
    if (currentOut < 0.1) {
      toast.info("Avance a agulha para escolher onde o vídeo deve começar.");
      return;
    }
    const nextDoc = trimStartAt(doc, safeSegIndex, time);
    if (nextDoc === doc) {
      toast.info("Mantenha pelo menos 0,2s de vídeo.");
      return;
    }
    update((current) => trimStartAt(current, safeSegIndex, time), "quickcut:trim-start");
    setCutMarkA(null);
    onSeek(0, nextDoc.segments[0].start);
    toast.success("Trecho antes da agulha removido!");
  }, [currentOut, doc, onSeek, safeSegIndex, time, update]);

  const handleTrimEnd = useCallback(() => {
    if (totalDuration - currentOut < 0.1) {
      toast.info("Volte a agulha para escolher onde o vídeo deve terminar.");
      return;
    }
    const nextDoc = trimEndAt(doc, safeSegIndex, time);
    if (nextDoc === doc) {
      toast.info("Mantenha pelo menos 0,2s de vídeo.");
      return;
    }
    update((current) => trimEndAt(current, safeSegIndex, time), "quickcut:trim-end");
    setCutMarkA(null);
    const lastIdx = nextDoc.segments.length - 1;
    onSeek(lastIdx, Math.max(nextDoc.segments[lastIdx].start, nextDoc.segments[lastIdx].end - 0.05));
    toast.success("Trecho depois da agulha removido!");
  }, [currentOut, doc, onSeek, safeSegIndex, time, totalDuration, update]);

  const handleDeleteSegment = useCallback(
    (targetIdx: number) => {
      if (doc.segments.length <= 1) {
        toast.info("O vídeo precisa manter pelo menos 1 parte.");
        return;
      }
      const nextDoc = deleteSegmentAt(doc, targetIdx);
      update((current) => deleteSegmentAt(current, targetIdx), "quickcut:delete");
      setCutMarkA(null);
      const nextIdx = Math.min(targetIdx, nextDoc.segments.length - 1);
      onSeek(nextIdx, nextDoc.segments[nextIdx].start);
      toast.success(`Parte ${targetIdx + 1} removida!`);
    },
    [doc, onSeek, update],
  );

  const handleMarkOrCutRange = useCallback(() => {
    if (cutMarkA === null) {
      setCutMarkA(currentOut);
      toast.info(`Ponto A marcado em ${timecode(currentOut)}. Agora mova a agulha e clique para arrancar o trecho!`);
      return;
    }
    const a = Math.min(cutMarkA, currentOut);
    const b = Math.max(cutMarkA, currentOut);
    if (b - a < 0.15) {
      toast.info("Mova a agulha para criar um intervalo antes de cortar.");
      return;
    }
    const nextDoc = cutOutputRange(doc, a, b);
    if (nextDoc === doc) {
      toast.info("Não é possível remover o vídeo inteiro — deixe pelo menos 0,2s.");
      return;
    }
    update((current) => cutOutputRange(current, a, b), "quickcut:range");
    setCutMarkA(null);
    const loc = locateOutputTime(nextDoc, Math.min(a, editDuration(nextDoc)));
    onSeek(loc.segmentIndex, loc.sourceTime);
    toast.success(`Trecho ${timecode(a)} – ${timecode(b)} arrancado do vídeo!`);
  }, [currentOut, cutMarkA, doc, onSeek, update]);

  const handleResetCuts = useCallback(() => {
    update(
      (current) => ({
        ...current,
        segments: [{ id: "original", start: 0, end: asset.duration }],
      }),
      "quickcut:reset",
    );
    setCutMarkA(null);
    onSeek(0, 0);
    toast.success("Cortes restaurados para o clipe completo!");
  }, [asset.duration, onSeek, update]);

  const playheadPct = clamp((currentOut / totalDuration) * 100, 0, 100);
  const selectionLeftPct =
    cutMarkA !== null ? clamp((Math.min(cutMarkA, currentOut) / totalDuration) * 100, 0, 100) : 0;
  const selectionWidthPct =
    cutMarkA !== null ? clamp((Math.abs(currentOut - cutMarkA) / totalDuration) * 100, 0, 100) : 0;

  return (
    <div
      className="space-y-3 rounded-2xl border bg-card p-4 shadow-xs"
      data-testid="quick-cut-bar"
    >
      {/* Top Row: Title, Duration, Reset & Open Full Editor */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="flex items-center gap-1.5 font-display text-sm font-bold">
            <Scissors className="size-4 text-rose-500" />
            Cortar &amp; Manipular Vídeo
          </h2>
          <Badge variant="secondary" className="text-[11px] tabular-nums" data-testid="quickcut-duration">
            {timecode(totalDuration)} • {doc.segments.length}{" "}
            {doc.segments.length === 1 ? "parte" : "partes"}
          </Badge>
          <span className="text-[11px] text-muted-foreground tabular-nums">
            Agulha: <strong>{timecode(currentOut)}</strong>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {isTrimmedOrSplit && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs text-muted-foreground"
              onClick={handleResetCuts}
              data-testid="quickcut-reset"
            >
              <RotateCcw className="size-3" />
              Restaurar Cortes
            </Button>
          )}
          {onOpenFullEditor && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 px-2.5 text-xs font-medium"
              disabled={openingFullEditor}
              onClick={() => void onOpenFullEditor()}
              data-testid="open-full-editor-button"
            >
              {openingFullEditor ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <ExternalLink className="size-3.5" />
              )}
              Editor Completo (Timeline)
            </Button>
          )}
        </div>
      </div>

      {/* Visual Interactive Timeline Bar with Real Audio Waveform */}
      <div className="space-y-1">
        <div
          ref={trackRef}
          role="slider"
          aria-label="Linha do tempo para cortar vídeo"
          aria-valuemin={0}
          aria-valuemax={Number(totalDuration.toFixed(2))}
          aria-valuenow={Number(currentOut.toFixed(2))}
          tabIndex={0}
          data-testid="quickcut-timeline"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            setDragging(true);
            seekFromClientX(e.clientX);
          }}
          onPointerMove={(e) => {
            if (dragging) seekFromClientX(e.clientX);
          }}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
          className="relative flex h-14 w-full cursor-pointer select-none items-stretch gap-0.5 overflow-hidden rounded-xl border bg-muted/40 p-1"
        >
          {segmentLayouts.map((item) => {
            const active = item.idx === safeSegIndex;
            return (
              <div
                key={item.seg.id}
                style={{ width: `${item.widthPct}%` }}
                className={cn(
                  "relative flex h-full items-center justify-around overflow-hidden rounded-lg px-0.5 transition-colors",
                  active
                    ? "bg-primary/15 ring-1 ring-primary/60"
                    : "bg-background/80 hover:bg-accent/50",
                )}
              >
                {item.bars.map((amp, i) => (
                  <span
                    key={i}
                    style={{ height: `${Math.round(amp * 78)}%` }}
                    className={cn(
                      "w-[2px] shrink-0 rounded-full",
                      active ? "bg-primary/65" : "bg-muted-foreground/35",
                    )}
                  />
                ))}
                <span className="pointer-events-none absolute top-0.5 left-1.5 rounded bg-background/80 px-1 text-[10px] font-semibold leading-tight text-foreground/80">
                  P{item.idx + 1}
                </span>
              </div>
            );
          })}

          {/* Highlighted A->B Cut Region */}
          {cutMarkA !== null && selectionWidthPct > 0.2 && (
            <div
              style={{ left: `${selectionLeftPct}%`, width: `${selectionWidthPct}%` }}
              className="pointer-events-none absolute inset-y-0 border-x-2 border-rose-500 bg-rose-500/30"
              data-testid="quickcut-range-highlight"
            />
          )}

          {/* Mark A pin */}
          {cutMarkA !== null && (
            <div
              style={{ left: `${clamp((cutMarkA / totalDuration) * 100, 0, 100)}%` }}
              className="pointer-events-none absolute inset-y-0 w-0.5 bg-rose-500"
            />
          )}

          {/* Live Playhead Needle */}
          <div
            style={{ left: `${playheadPct}%` }}
            className="pointer-events-none absolute inset-y-0 z-10 w-0.5 -translate-x-1/2 bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.9)]"
          >
            <div className="absolute -top-0.5 left-1/2 size-2.5 -translate-x-1/2 rounded-full bg-amber-500 ring-2 ring-background" />
          </div>
        </div>
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>Clique ou arraste na barra de áudio acima para posicionar a agulha</span>
          <span className="tabular-nums">0:00.00 – {timecode(totalDuration)}</span>
        </div>
      </div>

      {/* 1-Click Cut Controls */}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          onClick={handleTrimStart}
          data-testid="quickcut-trim-start"
          title="Remove tudo antes da agulha"
        >
          <ArrowLeftToLine className="size-3.5" />
          Cortar Antes
        </Button>

        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="h-8 gap-1.5 text-xs font-semibold"
          onClick={handleSplitHere}
          data-testid="quickcut-split"
          title="Divide o vídeo em duas partes na posição da agulha"
        >
          <Scissors className="size-3.5 text-rose-500" />
          Dividir Aqui
        </Button>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 gap-1.5 text-xs"
          onClick={handleTrimEnd}
          data-testid="quickcut-trim-end"
          title="Remove tudo depois da agulha"
        >
          <ArrowRightToLine className="size-3.5" />
          Cortar Depois
        </Button>

        <Button
          type="button"
          variant={cutMarkA !== null ? "destructive" : "outline"}
          size="sm"
          className="h-8 gap-1.5 text-xs font-medium"
          onClick={handleMarkOrCutRange}
          data-testid="quickcut-range"
        >
          {cutMarkA !== null ? (
            <>
              <Scissors className="size-3.5" />
              Arrancar Trecho ({timecode(Math.min(cutMarkA, currentOut))} – {timecode(Math.max(cutMarkA, currentOut))})
            </>
          ) : (
            <>
              <MapPin className="size-3.5 text-rose-500" />
              Cortar Meio (Marcar A)
            </>
          )}
        </Button>

        {cutMarkA !== null && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs text-muted-foreground"
            onClick={() => setCutMarkA(null)}
            data-testid="quickcut-cancel-range"
          >
            <X className="size-3.5" />
          </Button>
        )}

        {doc.segments.length > 1 && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 text-xs text-destructive hover:text-destructive"
            onClick={() => handleDeleteSegment(safeSegIndex)}
            data-testid="quickcut-delete-segment"
          >
            <Trash2 className="size-3.5" />
            Apagar Parte {safeSegIndex + 1}
          </Button>
        )}
      </div>

      {/* Segment Pills (shown when clip is split into multiple parts) */}
      {doc.segments.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5 border-t pt-2.5" data-testid="quickcut-segments-list">
          {segmentLayouts.map((item) => {
            const active = item.idx === safeSegIndex;
            return (
              <div
                key={item.seg.id}
                className={cn(
                  "inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] transition-colors",
                  active
                    ? "border-foreground bg-accent font-semibold"
                    : "bg-muted/40 text-muted-foreground hover:bg-accent/50",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSeek(item.idx, item.seg.start)}
                  className="tabular-nums"
                  data-testid={`quickcut-segment-pill-${item.idx}`}
                >
                  Parte {item.idx + 1} ({timecode(item.len)})
                </button>
                <button
                  type="button"
                  onClick={() => handleDeleteSegment(item.idx)}
                  aria-label={`Remover Parte ${item.idx + 1}`}
                  data-testid={`quickcut-segment-delete-${item.idx}`}
                  className="ml-0.5 rounded p-0.5 text-muted-foreground hover:bg-destructive/15 hover:text-destructive"
                >
                  <X className="size-3" />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
