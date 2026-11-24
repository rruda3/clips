"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  Captions,
  CaptionsOff,
  Check,
  Download,
  Film,
  Flame,
  Gamepad2,
  Loader2,
  MessageSquarePlus,
  Music,
  Search,
  Sliders,
  Sparkles,
  SplitSquareVertical,
  Trash2,
  Volume2,
  Wand2,
  Zap,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/app/app-shell";
import { ClipCover } from "@/components/app/clip-cover";
import { PageLoading } from "@/components/app/page-state";
import { Preview } from "@/components/editor/preview";
import { QuickCutBar } from "@/components/editor/quick-cut-bar";
import { editorRequest, useEditor } from "@/components/editor/use-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useSession } from "@/lib/auth-client";
import {
  editDuration,
  outputTime,
  timecode,
  uid,
  type EditDocument,
  type ExportJob,
  type GameplayLayoutMode,
  type MusicId,
  type SfxId,
  type VibeKind,
  type ViralEditConfig,
  type WebcamRegion,
} from "@/lib/editor/document";
import { fetchGenerations, type GenerationSummary } from "@/lib/generations";
import { cn } from "@/lib/utils";

interface TaskClip {
  id: string;
  task_id: string;
  filename: string;
  duration: number;
  start_time: string;
  end_time: string;
  virality_score?: number;
  hook_title?: string | null;
  transcript_text?: string | null;
}

interface ViralReport {
  used_gemini: boolean;
  peaks_count: number;
  tension_count: number;
  fail_moments_count: number;
  speed_ramps_count: number;
  dead_air_trimmed_count: number;
  webcam_detected: boolean;
}

const SFX_ITEMS: { id: SfxId; label: string; emoji: string }[] = [
  { id: "vine_boom", label: "Vine Boom", emoji: "💥" },
  { id: "record_scratch", label: "Record Scratch", emoji: "💿" },
  { id: "windows_error", label: "Erro Windows", emoji: "🪟" },
  { id: "crickets", label: "Grilo", emoji: "🦗" },
  { id: "censor_beep", label: "Bipe Censura", emoji: "🤬" },
  { id: "sad_violin", label: "Violino Triste", emoji: "🎻" },
  { id: "tension_riser", label: "Riser Tensão", emoji: "📈" },
  { id: "whoosh", label: "Whoosh Zoom", emoji: "💨" },
];

const MUSIC_ITEMS: { id: MusicId; label: string; desc: string }[] = [
  { id: "none", label: "Sem Música", desc: "Apenas áudio original e SFX" },
  { id: "wii_quirky", label: "Nintendo / Wii (Zoeira)", desc: "Fundo leve e cômico para conversa e caos" },
  { id: "sad_drama", label: "Triste / Dramática (P&B)", desc: "Ideal para Momento Fail e tela preta e branca" },
  { id: "tension_suspense", label: "Suspense / Clutch", desc: "Pulso grave para momentos de tensão" },
  { id: "hype_phonk", label: "Phonk / Hype (Ação)", desc: "Batida rápida para jogadas insanas" },
  { id: "lofi_chill", label: "Lo-Fi / Elevador", desc: "Fundo suave com auto-ducking" },
];

const VIBES: { id: VibeKind; label: string; desc: string }[] = [
  { id: "zoeira", label: "😂 Zoeira com a Molecada", desc: "Zooms secos, subtextos irônicos, música estilo Wii e SFX de meme" },
  { id: "fail", label: "💀 Momento Fail (P&B)", desc: "Foco em travar frame em Preto e Branco + música triste antes da falha" },
  { id: "hype", label: "💥 Explosão & Caos", desc: "Cortes secos no auge da explosão/grito, screen shake e Phonk" },
  { id: "clutch", label: "🎯 Tensão & Clutch", desc: "Slow zoom-in de tensão com riser grave + explosão no desfecho" },
];

function ensureViralConfig(doc: EditDocument): ViralEditConfig {
  return (
    doc.viral ?? {
      mode: "hybrid",
      vibe: "zoeira",
      layoutMode: "off",
      webcamBox: { x: 0.02, y: 0.03, w: 0.26, h: 0.28, zoom: 1 },
      layoutSwitches: [],
      cameraKeyframes: [],
      timeEffects: [],
      subtexts: [],
      speakerColors: { "Player 1": "#FACC15", "Player 2": "#38BDF8" },
      sfx: [],
      music: { trackId: "none", volume: 0.28, autoDuck: true },
      voiceEffects: [],
      suggestions: [],
    }
  );
}

export default function TikTokStudioPage() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <PageLoading />
        </AppShell>
      }
    >
      <TikTokStudioContent />
    </Suspense>
  );
}

function TikTokStudioContent() {
  const { data: session, isPending } = useSession();
  const searchParams = useSearchParams();
  const requestedTaskId = searchParams.get("task");
  const requestedClipId = searchParams.get("clip");
  const deepLinkedRef = useRef<string | null>(null);
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [loadingTasks, setLoadingTasks] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedTask, setSelectedTask] = useState<GenerationSummary | null>(null);
  const [clips, setClips] = useState<TaskClip[]>([]);
  const [loadingClips, setLoadingClips] = useState(false);
  const [activeClip, setActiveClip] = useState<TaskClip | null>(null);

  useEffect(() => {
    if (!session?.user?.id) {
      setTasks([]);
      setLoadingTasks(false);
      return;
    }
    setLoadingTasks(true);
    fetchGenerations()
      .then((list) => {
        // Only videos that already have generated cuts
        const readyWithClips = list.filter(
          (item) => item.status === "completed" && (item.clips_count ?? 0) > 0,
        );
        setTasks(readyWithClips);
      })
      .catch(() => toast.error("Não foi possível carregar os vídeos cortados."))
      .finally(() => setLoadingTasks(false));
  }, [session?.user?.id]);

  const openTaskClips = useCallback(async (task: GenerationSummary, autoSelectClipId?: string | null) => {
    setSelectedTask(task);
    setActiveClip(null);
    setLoadingClips(true);
    try {
      const response = await fetch(`/api/tasks/${task.id}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Falha ao buscar clipes");
      const data = await response.json();
      const loadedClips: TaskClip[] = Array.isArray(data.clips) ? data.clips : [];
      setClips(loadedClips);
      if (autoSelectClipId) {
        const match = loadedClips.find((c) => c.id === autoSelectClipId) ?? null;
        if (match) setActiveClip(match);
      }
    } catch {
      toast.error("Não foi possível carregar os cortes deste vídeo.");
      setClips([]);
    } finally {
      setLoadingClips(false);
    }
  }, []);

  useEffect(() => {
    if (!requestedTaskId || loadingTasks || tasks.length === 0) return;
    const key = `${requestedTaskId}:${requestedClipId ?? ""}`;
    if (deepLinkedRef.current === key) return;
    const found = tasks.find((t) => t.id === requestedTaskId);
    if (!found) return;
    deepLinkedRef.current = key;
    void openTaskClips(found, requestedClipId);
  }, [loadingTasks, openTaskClips, requestedClipId, requestedTaskId, tasks]);

  const filteredTasks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tasks;
    return tasks.filter((t) => (t.source_title || "").toLowerCase().includes(q));
  }, [tasks, search]);

  if (isPending || loadingTasks) {
    return (
      <AppShell>
        <PageLoading />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6">
        {activeClip && selectedTask ? (
          <ViralWorkbench
            key={`${activeClip.id}:${activeClip.filename}`}
            task={selectedTask}
            clip={activeClip}
            onBack={() => setActiveClip(null)}
            onClipExtended={(updated) => {
              setActiveClip(updated);
              setClips((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
            }}
          />
        ) : selectedTask ? (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b pb-4">
              <div className="flex items-center gap-3">
                <Button variant="outline" size="sm" onClick={() => setSelectedTask(null)}>
                  <ArrowLeft className="mr-1.5 size-4" />
                  Voltar aos Vídeos
                </Button>
                <div>
                  <h1 className="font-display text-xl font-bold tracking-tight">
                    {selectedTask.source_title || "Vídeo Cortado"}
                  </h1>
                  <p className="text-xs text-muted-foreground">
                    Escolha um corte abaixo e clique em <strong>Turbinar para TikTok</strong> para aplicar edição carismática com IA
                  </p>
                </div>
              </div>
              <Badge variant="secondary" className="gap-1.5 px-3 py-1">
                <Film className="size-3.5" />
                {clips.length} {clips.length === 1 ? "corte pronto" : "cortes prontos"}
              </Badge>
            </div>

            {loadingClips ? (
              <PageLoading />
            ) : clips.length === 0 ? (
              <div className="rounded-2xl border bg-card p-10 text-center">
                <p className="text-sm text-muted-foreground">Nenhum corte encontrado neste vídeo.</p>
              </div>
            ) : (
              <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {clips.map((clip, index) => (
                  <div
                    key={clip.id}
                    className="group flex flex-col overflow-hidden rounded-2xl border bg-card transition-all hover:border-foreground/40 hover:shadow-md"
                  >
                    <div className="relative aspect-[9/16] w-full overflow-hidden bg-black">
                      <ClipCover
                        src={`/api/tasks/${selectedTask.id}/clips/${clip.id}/file`}
                        className="size-full object-cover"
                      />
                      <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/75 to-transparent p-3 text-white">
                        <span className="rounded-md bg-black/60 px-2 py-0.5 text-xs font-semibold">
                          Corte #{index + 1}
                        </span>
                        {typeof clip.virality_score === "number" && clip.virality_score > 0 && (
                          <span className="rounded-md bg-amber-500/90 px-2 py-0.5 text-xs font-bold text-black">
                            🔥 {clip.virality_score}
                          </span>
                        )}
                      </div>
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/40 to-transparent p-3 text-white">
                        <p className="line-clamp-2 text-xs font-medium">
                          {clip.hook_title || clip.transcript_text || `${clip.start_time} - ${clip.end_time}`}
                        </p>
                      </div>
                    </div>
                    <div className="flex flex-col gap-2 p-3.5">
                      <Button
                        onClick={() => setActiveClip(clip)}
                        className="w-full gap-2 font-semibold"
                        data-testid={`boost-tiktok-${clip.id}`}
                      >
                        <Flame className="size-4" />
                        Turbinar para TikTok
                      </Button>
                      <Button asChild variant="outline" size="sm" className="w-full text-xs">
                        <Link href={`/tasks/${selectedTask.id}/edit/${clip.id}`}>
                          Abrir no Editor Padrão
                        </Link>
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-6">
            <div className="flex flex-col gap-4 border-b pb-5 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="flex size-9 items-center justify-center rounded-xl bg-amber-500/15 text-amber-500">
                    <Flame className="size-5" />
                  </span>
                  <h1 className="font-display text-2xl font-bold tracking-tight">
                    Estúdio TikTok & Gameplay
                  </h1>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  Acesse seus vídeos que já possuem cortes prontos e transforme qualquer clipe num edit viral e carismático para TikTok, Reels e Shorts (Automático ou Híbrido).
                </p>
              </div>
              <div className="relative w-full sm:w-72">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Buscar vídeo com cortes..."
                  className="pl-9"
                />
              </div>
            </div>

            {filteredTasks.length === 0 ? (
              <div className="flex flex-col items-center justify-center rounded-2xl border bg-card px-6 py-16 text-center">
                <Gamepad2 className="mb-3 size-10 text-muted-foreground" />
                <h2 className="text-base font-semibold">Nenhum vídeo com cortes prontos encontrado</h2>
                <p className="mt-1 max-w-md text-xs text-muted-foreground">
                  Esta aba mostra exclusivamente os vídeos que já tiveram seus cortes gerados. Crie um corte (inclusive usando o botão Gameplay) para turbiná-lo aqui!
                </p>
                <Button asChild className="mt-4">
                  <Link href="/">Criar Novos Cortes</Link>
                </Button>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {filteredTasks.map((task) => (
                  <button
                    key={task.id}
                    type="button"
                    onClick={() => void openTaskClips(task)}
                    data-testid={`tiktok-task-${task.id}`}
                    className="group flex flex-col justify-between rounded-2xl border bg-card p-5 text-left transition-all hover:border-foreground/50 hover:shadow-md"
                  >
                    <div className="space-y-3">
                      <div className="flex items-start justify-between gap-2">
                        <Badge variant="secondary" className="gap-1">
                          <Film className="size-3" />
                          {task.clips_count} {task.clips_count === 1 ? "corte" : "cortes"}
                        </Badge>
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-semibold text-amber-500">
                          <Flame className="size-3" />
                          Pronto p/ TikTok
                        </span>
                      </div>
                      <h2 className="line-clamp-2 font-display text-base font-semibold group-hover:text-foreground">
                        {task.source_title || "Vídeo sem título"}
                      </h2>
                    </div>
                    <div className="mt-5 flex items-center justify-between border-t pt-3 text-xs font-medium text-muted-foreground group-hover:text-foreground">
                      <span>Ver todos os clipes e turbinar</span>
                      <span>→</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </AppShell>
  );
}

function ViralWorkbench({
  task,
  clip,
  onBack,
  onClipExtended,
}: {
  task: GenerationSummary;
  clip: TaskClip;
  onBack: () => void;
  onClipExtended?: (updated: TaskClip) => void;
}) {
  const endpoint = `/api/tasks/${task.id}/clips/${clip.id}/editor`;
  const editor = useEditor(endpoint);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [segmentIndex, setSegmentIndex] = useState(0);
  const [time, setTime] = useState(0);
  const [seekRequest, setSeekRequest] = useState({ time: 0, nonce: 0 });

  const [mode, setMode] = useState<"auto" | "hybrid">("auto");
  const [vibe, setVibe] = useState<VibeKind>("zoeira");
  const [splitCam, setSplitCam] = useState(true);
  const [coldOpen, setColdOpen] = useState(true);
  const [explosionCut, setExplosionCut] = useState(true);
  const [runningAi, setRunningAi] = useState(false);
  const [extending, setExtending] = useState(false);
  const [report, setReport] = useState<ViralReport | null>(null);
  const [subtextInput, setSubtextInput] = useState("Ele não sabia...");
  const [subtextVariant, setSubtextVariant] = useState<"thought" | "tracker" | "censor_bar">("thought");
  const [exporting, setExporting] = useState(false);
  const [exportJob, setExportJob] = useState<ExportJob | null>(null);

  const doc = editor.document;
  const asset = editor.asset;
  const restoredExtendRef = useRef(false);

  useEffect(() => {
    if (!doc || !asset || asset.status !== "ready" || restoredExtendRef.current) return;
    const storageKey = `tiktok-extend:${clip.id}:${clip.filename}`;
    const raw = window.sessionStorage.getItem(storageKey);
    if (!raw) return;
    restoredExtendRef.current = true;
    window.sessionStorage.removeItem(storageKey);
    try {
      const payload = JSON.parse(raw) as { viral?: ViralEditConfig; appliedBefore?: number };
      const shift = Number(payload.appliedBefore || 0);
      const savedViral = payload.viral;
      if (!savedViral) return;
      editor.update((current) => ({
        ...current,
        viral: {
          ...savedViral,
          cameraKeyframes: savedViral.cameraKeyframes.map((k) => ({
            ...k,
            start: Number((k.start + shift).toFixed(2)),
            end: Number((k.end + shift).toFixed(2)),
          })),
          timeEffects: savedViral.timeEffects.map((t) => ({
            ...t,
            start: Number((t.start + shift).toFixed(2)),
            end: Number((t.end + shift).toFixed(2)),
          })),
          subtexts: savedViral.subtexts.map((s) => ({
            ...s,
            start: Number((s.start + shift).toFixed(2)),
            end: Number((s.end + shift).toFixed(2)),
          })),
          sfx: savedViral.sfx.map((x) => ({
            ...x,
            start: Number((x.start + shift).toFixed(2)),
          })),
        },
      }), "viral:extend-restore");
    } catch {
      /* ignore corrupt extension restore payload */
    }
  }, [asset, clip.filename, clip.id, doc, editor]);

  const extendClipRange = useCallback(
    async (beforeSeconds: number, afterSeconds: number) => {
      if (extending) return;
      setExtending(true);
      try {
        const res = await editorRequest<{
          clip: TaskClip;
          applied_before: number;
          applied_after: number;
        }>(`/api/tasks/${task.id}/clips/${clip.id}/extend`, "POST", {
          before_seconds: beforeSeconds,
          after_seconds: afterSeconds,
        });
        if (doc?.viral) {
          window.sessionStorage.setItem(
            `tiktok-extend:${res.clip.id}:${res.clip.filename}`,
            JSON.stringify({ viral: doc.viral, appliedBefore: res.applied_before }),
          );
        }
        toast.success(
          `Corte estendido (${res.applied_before > 0 ? `-${res.applied_before}s início` : ""}${res.applied_before > 0 && res.applied_after > 0 ? " / " : ""}${res.applied_after > 0 ? `+${res.applied_after}s fim` : ""})! Recarregando vídeo...`,
        );
        onClipExtended?.(res.clip);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Não foi possível estender este corte.");
      } finally {
        setExtending(false);
      }
    },
    [clip.id, doc?.viral, extending, onClipExtended, task.id],
  );

  useEffect(() => {
    if (!exportJob || (exportJob.status !== "queued" && exportJob.status !== "rendering")) {
      return;
    }
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const next = await editorRequest<ExportJob>(`${endpoint}/exports/${exportJob.id}`);
        if (cancelled) return;
        setExportJob(next);
        if (next.status === "completed") {
          toast.success("Vídeo TikTok 9:16 renderizado com sucesso!");
        } else if (next.status === "failed") {
          toast.error(next.error || "Falha ao renderizar o vídeo TikTok.");
        }
      } catch {
        /* keep polling until cancelled or completed */
      }
    }, 1500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [endpoint, exportJob]);

  const router = useRouter();
  const [openingFullEditor, setOpeningFullEditor] = useState(false);
  const seek = useCallback((target: number) => {
    setSeekRequest((prev) => ({ time: Math.max(0, target), nonce: prev.nonce + 1 }));
  }, []);

  const seekToSegment = useCallback(
    (idx: number, sourceTime: number) => {
      setSegmentIndex(idx);
      setTime(sourceTime);
      seek(sourceTime);
    },
    [seek],
  );

  const openFullEditor = useCallback(async () => {
    if (openingFullEditor) return;
    setOpeningFullEditor(true);
    try {
      await editor.save();
      router.push(`/tasks/${task.id}/edit?clip=${clip.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao salvar antes de abrir o editor.");
      setOpeningFullEditor(false);
    }
  }, [clip.id, editor, openingFullEditor, router, task.id]);

  const activeAudioRef = useRef<HTMLAudioElement | null>(null);

  const stopPreviewAudio = useCallback(() => {
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }
  }, []);

  useEffect(() => stopPreviewAudio, [stopPreviewAudio]);

  const previewAudio = useCallback(
    (kind: "sfx" | "music", id: string) => {
      stopPreviewAudio();
      if (id === "none") return;
      const audio = new Audio(`/api/tasks/viral-audio/${kind}/${id}`);
      audio.volume = 0.7;
      activeAudioRef.current = audio;
      audio.onended = () => {
        if (activeAudioRef.current === audio) activeAudioRef.current = null;
      };
      void audio.play().catch(() => {});
    },
    [stopPreviewAudio],
  );

  const updateWebcamBox = useCallback(
    (patch: Partial<WebcamRegion>) => {
      editor.update((c) => {
        const v = ensureViralConfig(c);
        const nextX = patch.x !== undefined ? Math.max(0, Math.min(1, patch.x)) : v.webcamBox.x;
        const nextY = patch.y !== undefined ? Math.max(0, Math.min(1, patch.y)) : v.webcamBox.y;
        const rawZoom = patch.zoom !== undefined ? patch.zoom : (v.webcamBox.zoom ?? 1);
        const nextZoom = Math.round(Math.max(0.5, Math.min(2.5, rawZoom)) * 100) / 100;
        return {
          ...c,
          viral: {
            ...v,
            webcamBox: { ...v.webcamBox, ...patch, x: nextX, y: nextY, zoom: nextZoom },
          },
        };
      }, "gesture:webcam");
    },
    [editor],
  );

  const runViralDirector = useCallback(
    async (selectedMode: "auto" | "hybrid") => {
      if (!doc || !asset) return;
      setRunningAi(true);
      try {
        const res = await editorRequest<{ document: EditDocument; report: ViralReport }>(
          `${endpoint}/viral`,
          "POST",
          {
            document: doc,
            mode: selectedMode,
            vibe,
            splitCam,
            coldOpen,
            explosionCut,
            useGemini: true,
          },
        );
        editor.update(() => res.document, "viral:ai");
        setReport(res.report);
        toast.success(
          selectedMode === "auto"
            ? "Edição Viral TikTok aplicada automaticamente!"
            : "Análise concluída! Confira as sugestões na bancada híbrida.",
        );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Erro ao rodar análise viral.");
      } finally {
        setRunningAi(false);
      }
    },
    [asset, coldOpen, doc, editor, endpoint, explosionCut, splitCam, vibe],
  );

  // 1-Click Presets at current playhead (`time`)
  const applyMomentoFailPreset = useCallback(() => {
    if (!doc || !asset) return;
    const t0 = Math.max(0, Math.min(asset.duration - 0.3, time));
    const t1 = Math.min(asset.duration, Number((t0 + 1.5).toFixed(2)));
    editor.update((current) => {
      const viral = ensureViralConfig(current);
      return {
        ...current,
        viral: {
          ...viral,
          timeEffects: [
            ...viral.timeEffects,
            {
              id: uid(),
              start: Number(t0.toFixed(2)),
              end: t1,
              kind: "freeze_bw",
              freezeDuration: 1.5,
              speed: 1,
              reason: "Preset Momento Fail (Freeze 1.5s + P&B)",
            },
          ],
          subtexts: [
            ...viral.subtexts,
            {
              id: uid(),
              start: Number(t0.toFixed(2)),
              end: t1,
              text: "Ele não sabia...",
              x: 0.5,
              y: 0.26,
              color: "#FACC15",
              variant: "thought",
            },
          ],
          sfx: [
            ...viral.sfx,
            {
              id: uid(),
              sfxId: "sad_violin",
              start: Number(t0.toFixed(2)),
              volume: 0.9,
            },
          ],
          music:
            viral.music.trackId === "none"
              ? { trackId: "sad_drama", volume: 0.32, autoDuck: true }
              : viral.music,
        },
      };
    }, "viral:fail-preset");
    previewAudio("sfx", "sad_violin");
    toast.success(`Preset "Momento Fail" (P&B + Drama) aplicado em ${timecode(t0)}!`);
  }, [asset, doc, editor, previewAudio, time]);

  const applyExplosionCutPreset = useCallback(() => {
    if (!doc || !asset) return;
    const cutPoint = Math.max(0.5, Math.min(asset.duration, Number((time + 0.2).toFixed(2))));
    const shakeStart = Math.max(0, Number((cutPoint - 0.35).toFixed(2)));
    editor.update((current) => {
      const viral = ensureViralConfig(current);
      const trimmed = current.segments
        .filter((s) => s.start < cutPoint - 0.04)
        .map((s) => (s.end > cutPoint ? { ...s, end: cutPoint } : s));
      return {
        ...current,
        segments: trimmed.length ? trimmed : current.segments,
        viral: {
          ...viral,
          cameraKeyframes: [
            ...viral.cameraKeyframes,
            {
              id: uid(),
              start: shakeStart,
              end: cutPoint,
              mode: "shake",
              zoom: 1.25,
              intensity: 0.8,
              target: "center",
              reason: "Corte Seco na Explosão",
            },
          ],
          sfx: [
            ...viral.sfx,
            {
              id: uid(),
              sfxId: "vine_boom",
              start: shakeStart,
              volume: 0.95,
            },
          ],
        },
      };
    }, "viral:explosion-cut");
    previewAudio("sfx", "vine_boom");
    toast.success(`Corte Seco na Explosão aplicado em ${timecode(cutPoint)}!`);
  }, [asset, doc, editor, previewAudio, time]);

  const addCameraEffectAtPlayhead = useCallback(
    (kfMode: "punch" | "slow_tension" | "shake" | "bw_dramatic") => {
      if (!doc || !asset) return;
      const span = kfMode === "slow_tension" ? 3.0 : kfMode === "shake" ? 0.35 : 0.85;
      const t0 = Math.max(0, Math.min(asset.duration - 0.1, Number(time.toFixed(2))));
      const t1 = Math.min(asset.duration, Number((t0 + span).toFixed(2)));
      editor.update((current) => {
        const viral = ensureViralConfig(current);
        return {
          ...current,
          viral: {
            ...viral,
            cameraKeyframes: [
              ...viral.cameraKeyframes,
              {
                id: uid(),
                start: t0,
                end: t1,
                mode: kfMode,
                zoom: kfMode === "punch" ? 1.25 : 1.2,
                intensity: kfMode === "shake" ? 0.75 : 0.5,
                target: "center",
                reason: `Adicionado manualmente em ${timecode(t0)}`,
              },
            ],
          },
        };
      }, `viral:cam-${kfMode}`);
      toast.success(`Efeito de câmera (${kfMode}) adicionado em ${timecode(t0)}!`);
    },
    [asset, doc, editor, time],
  );

  const addSfxAtPlayhead = useCallback(
    (sfxId: SfxId) => {
      if (!doc) return;
      const t0 = Math.max(0, Number(time.toFixed(2)));
      editor.update((current) => {
        const viral = ensureViralConfig(current);
        return {
          ...current,
          viral: {
            ...viral,
            sfx: [...viral.sfx, { id: uid(), sfxId, start: t0, volume: 0.85 }],
          },
        };
      }, `viral:sfx-${sfxId}`);
      previewAudio("sfx", sfxId);
      toast.success(`SFX "${sfxId}" inserido em ${timecode(t0)}!`);
    },
    [doc, editor, previewAudio, time],
  );

  const addSubtextAtPlayhead = useCallback(() => {
    if (!doc || !asset || !subtextInput.trim()) return;
    const t0 = Math.max(0, Math.min(asset.duration - 0.2, Number(time.toFixed(2))));
    const t1 = Math.min(asset.duration, Number((t0 + 1.8).toFixed(2)));
    editor.update((current) => {
      const viral = ensureViralConfig(current);
      return {
        ...current,
        viral: {
          ...viral,
          subtexts: [
            ...viral.subtexts,
            {
              id: uid(),
              start: t0,
              end: t1,
              text: subtextInput.trim(),
              x: 0.5,
              y: subtextVariant === "censor_bar" ? 0.5 : 0.28,
              color: subtextVariant === "tracker" ? "#4ADE80" : "#FACC15",
              variant: subtextVariant,
            },
          ],
          ...(subtextVariant === "censor_bar"
            ? { sfx: [...viral.sfx, { id: uid(), sfxId: "censor_beep", start: t0, volume: 0.9 }] }
            : {}),
        },
      };
    }, "viral:subtext");
    toast.success(`Texto "${subtextInput.trim()}" adicionado em ${timecode(t0)}!`);
  }, [asset, doc, editor, subtextInput, subtextVariant, time]);

  const setLayoutMode = useCallback(
    (nextLayout: GameplayLayoutMode) => {
      editor.update((current) => {
        const viral = ensureViralConfig(current);
        return {
          ...current,
          viral: { ...viral, layoutMode: nextLayout },
        };
      }, "viral:layout");
    },
    [editor],
  );

  const setAspectFormat = useCallback(
    (nextAspect: EditDocument["framing"]["aspect"]) => {
      editor.update(
        (current) => ({
          ...current,
          framing: { ...current.framing, aspect: nextAspect },
        }),
        "framing:aspect",
      );
    },
    [editor],
  );

  const setMusicTrack = useCallback(
    (trackId: MusicId) => {
      editor.update((current) => {
        const viral = ensureViralConfig(current);
        return {
          ...current,
          viral: {
            ...viral,
            music: { ...viral.music, trackId },
          },
        };
      }, "viral:music");
      if (trackId === "none") stopPreviewAudio();
      else previewAudio("music", trackId);
    },
    [editor, previewAudio, stopPreviewAudio],
  );

  const clearAllAppliedEffects = useCallback(() => {
    stopPreviewAudio();
    editor.update(
      (c) => ({
        ...c,
        viral: {
          ...ensureViralConfig(c),
          timeEffects: [],
          cameraKeyframes: [],
          sfx: [],
          subtexts: [],
          voiceEffects: [],
          layoutSwitches: [],
          music: { trackId: "none", volume: 0.28, autoDuck: true },
        },
      }),
      "viral:clear-all",
    );
    toast.success("Todos os efeitos, textos e sons foram removidos!");
  }, [editor, stopPreviewAudio]);

  const handleSaveAndExport = useCallback(async () => {
    if (!doc || !asset) return;
    setExporting(true);
    try {
      await editor.save();
      const job = await editorRequest<ExportJob>(`${endpoint}/exports`, "POST", {
        basis: asset.basis,
        preset: doc.framing.aspect === "vertical" ? "tiktok" : "reels",
        document: doc,
      });
      setExportJob(job);
      const ratioLabel =
        doc.framing.aspect === "square" ? "1:1" : doc.framing.aspect === "original" ? "16:9" : "9:16";
      toast.success(`Renderização ${ratioLabel} iniciada! Acompanhe o progresso abaixo.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao exportar vídeo.");
    } finally {
      setExporting(false);
    }
  }, [asset, doc, editor, endpoint]);

  if (!asset || asset.status === "preparing" || asset.status === "unprepared" || !doc) {
    return (
      <div className="space-y-4">
        <Button variant="outline" size="sm" onClick={onBack}>
          <ArrowLeft className="mr-1.5 size-4" />
          Voltar aos Cortes
        </Button>
        <PageLoading />
      </div>
    );
  }

  const viral = ensureViralConfig(doc);
  const latestJob = exportJob ?? asset.jobs?.[0];
  const safeSegmentIndex = Math.min(segmentIndex, Math.max(0, doc.segments.length - 1));
  const playheadOutputTime = outputTime(doc, safeSegmentIndex, time);

  return (
    <div className="space-y-6">
      {/* Top Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={onBack}>
            <ArrowLeft className="mr-1.5 size-4" />
            Cortes do Vídeo
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-display text-lg font-bold">
                🔥 Turbinar para TikTok / Reels / Shorts
              </h1>
              <Badge variant="secondary">{timecode(editDuration(doc))}</Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              {editor.status} • Agulha atual: <strong className="tabular-nums">{timecode(playheadOutputTime)}</strong>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-xl border bg-muted/50 p-1">
            <button
              type="button"
              onClick={() => setMode("auto")}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors",
                mode === "auto" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Wand2 className="size-3.5" />
              Modo Automático (1-Clique)
            </button>
            <button
              type="button"
              onClick={() => setMode("hybrid")}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors",
                mode === "hybrid" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Sliders className="size-3.5" />
              Modo Híbrido (Copiloto)
            </button>
          </div>

          <Button variant="outline" size="sm" onClick={editor.undo} disabled={!editor.canUndo}>
            Desfazer
          </Button>
          <Button
            onClick={() => void handleSaveAndExport()}
            disabled={exporting || latestJob?.status === "queued" || latestJob?.status === "rendering"}
            className="gap-1.5 font-semibold"
            data-testid="export-tiktok-button"
          >
            {exporting || latestJob?.status === "queued" || latestJob?.status === "rendering" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Download className="size-4" />
            )}
            Exportar ({doc.framing.aspect === "square" ? "1:1" : doc.framing.aspect === "original" ? "16:9" : "9:16"})
          </Button>
        </div>
      </div>

      {/* Main Grid: Live 9:16 Preview on Left, Viral Controls on Right */}
      <div className="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        {/* Left: Live 9:16 Canvas Preview */}
        <div className="flex flex-col gap-3">
          <div className="h-[580px] w-full">
            <Preview
              doc={doc}
              asset={asset}
              endpoint={endpoint}
              segmentIndex={segmentIndex}
              select={setSegmentIndex}
              time={time}
              onTime={setTime}
              seekRequest={seekRequest}
              tab="effects"
              update={editor.update}
              videoRef={videoRef}
            />
          </div>

          <Button
            type="button"
            variant={doc.captions.enabled ? "outline" : "secondary"}
            size="sm"
            className="w-full justify-center gap-2 text-xs font-semibold"
            onClick={() =>
              editor.update(
                (c) => ({
                  ...c,
                  captions: { ...c.captions, enabled: !c.captions.enabled },
                }),
                "captions:toggle",
              )
            }
            data-testid="tiktok-toggle-captions"
          >
            {doc.captions.enabled ? (
              <>
                <CaptionsOff className="size-3.5" />
                Tirar Legenda
              </>
            ) : (
              <>
                <Captions className="size-3.5" />
                Legenda Removida (Ativar Legenda)
              </>
            )}
          </Button>

          {latestJob && (
            <div className="rounded-xl border bg-card p-3 text-xs" data-testid="export-status-box">
              <div className="flex items-center justify-between font-medium">
                <span>
                  Exportação TikTok:{" "}
                  {latestJob.status === "completed"
                    ? "Concluída!"
                    : latestJob.status === "failed"
                      ? `Falhou${latestJob.error ? ` (${latestJob.error})` : ""}`
                      : `Renderizando (${latestJob.progress ?? 0}%)`}
                </span>
                {latestJob.status === "completed" && (
                  <a
                    href={`${endpoint}/exports/${latestJob.id}/file`}
                    data-testid="download-tiktok-mp4"
                    className="inline-flex items-center gap-1 rounded-md bg-foreground px-2.5 py-1 font-semibold text-background"
                  >
                    <Download className="size-3" /> Baixar MP4
                  </a>
                )}
              </div>
            </div>
          )}

          {/* Extend Clip Boundaries from Cached Source */}
          <div className="rounded-xl border bg-card p-3 text-xs" data-testid="extend-clip-box">
            <div className="mb-2 flex items-center justify-between">
              <span className="font-semibold">Estender Corte (do vídeo em cache)</span>
              <span className="text-[11px] text-muted-foreground tabular-nums">
                {clip.start_time} – {clip.end_time}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {[3, 5, 10, 15].map((sec) => (
                <div key={sec} className="contents">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-[11px]"
                    disabled={extending}
                    onClick={() => void extendClipRange(sec, 0)}
                    data-testid={`extend-before-${sec}`}
                  >
                    {extending ? <Loader2 className="size-3 animate-spin" /> : `⏪ -${sec}s Início`}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-[11px]"
                    disabled={extending}
                    onClick={() => void extendClipRange(0, sec)}
                    data-testid={`extend-after-${sec}`}
                  >
                    {extending ? <Loader2 className="size-3 animate-spin" /> : `+${sec}s Fim ⏩`}
                  </Button>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right: Quick Cut + Auto / Hybrid Controls */}
        <div className="space-y-5">
          <QuickCutBar
            doc={doc}
            asset={asset}
            time={time}
            segmentIndex={safeSegmentIndex}
            onSeek={seekToSegment}
            update={editor.update}
            onOpenFullEditor={openFullEditor}
            openingFullEditor={openingFullEditor}
          />

          {/* AI Director Card (Google Gemini + Radar Acústico) */}
          <div className="rounded-2xl border bg-card p-5 shadow-xs">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 font-display text-base font-bold">
                  <Sparkles className="size-4 text-amber-500" />
                  Diretor Viral IA (Google Gemini + Radar de Barulhos)
                </h2>
                <p className="text-xs text-muted-foreground">
                  Analisa gritos, explosões, silêncios tensos e reações na câmera para editar o corte automaticamente ou sugerir na timeline.
                </p>
              </div>
              <Button
                onClick={() => void runViralDirector(mode)}
                disabled={runningAi}
                className="gap-2 font-semibold"
                data-testid="run-viral-ai-button"
              >
                {runningAi ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Analisando Vídeo e Áudio...
                  </>
                ) : (
                  <>
                    <Flame className="size-4" />
                    {mode === "auto" ? "Aplicar Edição Mágica 1-Clique" : "Escanear e Sugerir Edições"}
                  </>
                )}
              </Button>
            </div>

            {/* Vibe selector */}
            <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {VIBES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setVibe(item.id)}
                  className={cn(
                    "flex flex-col rounded-xl border p-3 text-left transition-all",
                    vibe === item.id
                      ? "border-foreground bg-accent/60 ring-1 ring-foreground"
                      : "hover:bg-accent/30",
                  )}
                >
                  <span className="text-xs font-bold">{item.label}</span>
                  <span className="mt-1 text-[11px] leading-snug text-muted-foreground">{item.desc}</span>
                </button>
              ))}
            </div>

            {/* Quick Auto Toggles */}
            <div className="mt-4 flex flex-wrap gap-4 border-t pt-3 text-xs">
              <label className="flex cursor-pointer items-center gap-2">
                <Switch checked={splitCam} onCheckedChange={setSplitCam} />
                <span>Dividir Câmera + Gameplay (9:16)</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2">
                <Switch checked={explosionCut} onCheckedChange={setExplosionCut} />
                <span>Corte Seco na Explosão</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2">
                <Switch checked={coldOpen} onCheckedChange={setColdOpen} />
                <span>Teaser de 1.5s no Início (Cold Open)</span>
              </label>
            </div>

            {report && (
              <div className="mt-3 flex flex-wrap gap-2 rounded-xl bg-muted/60 p-2.5 text-xs">
                <Badge variant="outline">🔊 {report.peaks_count} picos de grito/explosão</Badge>
                <Badge variant="outline">📈 {report.tension_count} janelas de tensão</Badge>
                <Badge variant="outline">💀 {report.fail_moments_count} momentos fail</Badge>
                <Badge variant="outline">✂️ {report.dead_air_trimmed_count} pausas limpas</Badge>
                <Badge variant="outline">
                  {report.used_gemini ? "✨ Gemini Multimodal Ativo" : "⚡ Radar Acústico Local"}
                </Badge>
              </div>
            )}
          </div>

          {/* 1-Click Charisma Presets & Camera Dynamics */}
          <div className="grid gap-4 md:grid-cols-2">
            {/* Presets de Ritmo e Câmera na Agulha */}
            <div className="space-y-3 rounded-2xl border bg-card p-4">
              <h3 className="flex items-center gap-2 text-sm font-bold">
                <Zap className="size-4 text-amber-500" />
                Efeitos Favoritos na Agulha ({timecode(playheadOutputTime)})
              </h3>
              <div className="grid grid-cols-2 gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={applyMomentoFailPreset}
                  className="justify-start gap-2 text-xs font-semibold"
                  data-testid="preset-momento-fail"
                >
                  💀 Momento Fail (P&B + Trava)
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={applyExplosionCutPreset}
                  className="justify-start gap-2 text-xs font-semibold"
                  data-testid="preset-explosion-cut"
                >
                  💥 Cortar na Explosão
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => addCameraEffectAtPlayhead("punch")}
                  className="justify-start gap-1.5 text-xs"
                >
                  🔍 Zoom-in Punch (125%)
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => addCameraEffectAtPlayhead("slow_tension")}
                  className="justify-start gap-1.5 text-xs"
                >
                  📈 Slow Zoom Tensão
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => addCameraEffectAtPlayhead("shake")}
                  className="justify-start gap-1.5 text-xs"
                >
                  📳 Screen Shake (Tremida)
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => addCameraEffectAtPlayhead("bw_dramatic")}
                  className="justify-start gap-1.5 text-xs"
                >
                  🎬 Tela Preta e Branca
                </Button>
              </div>
            </div>

            {/* Alternador de Formato e Layout (Split Câmera + Gameplay) */}
            <div className="space-y-3 rounded-2xl border bg-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="flex items-center gap-2 text-sm font-bold">
                  <SplitSquareVertical className="size-4 text-sky-400" />
                  Formato &amp; Câmera / Gameplay
                </h3>
                <div className="inline-flex rounded-lg border bg-muted/40 p-0.5 text-[11px]" data-testid="format-aspect-group">
                  {(
                    [
                      { id: "vertical", label: "9:16" },
                      { id: "square", label: "1:1" },
                      { id: "original", label: "16:9" },
                    ] as const
                  ).map((fmt) => (
                    <button
                      key={fmt.id}
                      type="button"
                      onClick={() => setAspectFormat(fmt.id)}
                      data-testid={`format-aspect-${fmt.id}`}
                      className={cn(
                        "rounded-md px-2 py-0.5 font-semibold transition-colors",
                        doc.framing.aspect === fmt.id
                          ? "bg-background text-foreground shadow-xs"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {fmt.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                {(
                  [
                    { id: "split", label: "📱 Split (Câmera + Jogo)" },
                    { id: "fullscreen_cam", label: "😎 Facecam Tela Cheia" },
                    { id: "pip", label: "🖼️ Câmera no Canto (PiP)" },
                    { id: "off", label: "🎮 Jogo Centralizado" },
                  ] as const
                ).map((item) => (
                  <Button
                    key={item.id}
                    variant={viral.layoutMode === item.id ? "default" : "outline"}
                    size="sm"
                    onClick={() => setLayoutMode(item.id)}
                    className="justify-start text-xs"
                    data-testid={`layout-mode-${item.id}`}
                  >
                    {item.label}
                  </Button>
                ))}
              </div>
              {viral.layoutMode !== "off" && (
                <div className="grid grid-cols-2 gap-2 pt-1 text-[11px] text-muted-foreground">
                  <label className="flex flex-col gap-1">
                    <span data-testid="webcam-x-label">Webcam X ({Math.round(viral.webcamBox.x * 100)}%)</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.01}
                      value={viral.webcamBox.x}
                      data-testid="webcam-x-slider"
                      onChange={(e) => updateWebcamBox({ x: Number(e.target.value) })}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span data-testid="webcam-y-label">Webcam Y ({Math.round(viral.webcamBox.y * 100)}%)</span>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.01}
                      value={viral.webcamBox.y}
                      data-testid="webcam-y-slider"
                      onChange={(e) => updateWebcamBox({ y: Number(e.target.value) })}
                    />
                  </label>
                  <div className="col-span-2 flex flex-col gap-1.5 pt-1">
                    <div className="flex items-center justify-between">
                      <span data-testid="webcam-zoom-label">
                        Zoom da Câmera ({(viral.webcamBox.zoom ?? 1).toFixed(2)}x)
                      </span>
                      <div className="flex items-center gap-1">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-6 px-2 text-[11px] gap-1"
                          disabled={(viral.webcamBox.zoom ?? 1) <= 0.5}
                          data-testid="webcam-zoom-out"
                          onClick={() => updateWebcamBox({ zoom: (viral.webcamBox.zoom ?? 1) - 0.15 })}
                        >
                          <ZoomOut className="size-3" />
                          Zoom Out
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-6 px-2 text-[11px] gap-1"
                          disabled={(viral.webcamBox.zoom ?? 1) >= 2.5}
                          data-testid="webcam-zoom-in"
                          onClick={() => updateWebcamBox({ zoom: (viral.webcamBox.zoom ?? 1) + 0.15 })}
                        >
                          <ZoomIn className="size-3" />
                          Zoom In
                        </Button>
                      </div>
                    </div>
                    <input
                      type="range"
                      min={0.5}
                      max={2.5}
                      step={0.05}
                      value={viral.webcamBox.zoom ?? 1}
                      data-testid="webcam-zoom-slider"
                      onChange={(e) => updateWebcamBox({ zoom: Number(e.target.value) })}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Sound Design: SFX 1-Click + Música de Fundo com Auto-Ducking */}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-3 rounded-2xl border bg-card p-4">
              <h3 className="flex items-center gap-2 text-sm font-bold">
                <Volume2 className="size-4 text-emerald-400" />
                Painel de SFX de Reação Rápida (1-Clique)
              </h3>
              <div className="grid grid-cols-2 gap-1.5">
                {SFX_ITEMS.map((item) => (
                  <Button
                    key={item.id}
                    variant="outline"
                    size="sm"
                    onClick={() => addSfxAtPlayhead(item.id)}
                    className="justify-start gap-1.5 text-xs"
                  >
                    <span>{item.emoji}</span>
                    <span className="truncate">{item.label}</span>
                  </Button>
                ))}
              </div>
            </div>

            <div className="space-y-3 rounded-2xl border bg-card p-4">
              <div className="flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-sm font-bold">
                  <Music className="size-4 text-purple-400" />
                  Música de Fundo & Auto-Ducking
                </h3>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Switch
                    checked={viral.music.autoDuck}
                    onCheckedChange={(autoDuck) =>
                      editor.update((c) => {
                        const v = ensureViralConfig(c);
                        return { ...c, viral: { ...v, music: { ...v.music, autoDuck } } };
                      }, "viral:duck")
                    }
                  />
                  Auto-Ducking
                </label>
              </div>
              <div className="grid gap-1.5">
                {MUSIC_ITEMS.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setMusicTrack(m.id)}
                    className={cn(
                      "flex items-center justify-between rounded-xl border px-3 py-2 text-left text-xs transition-colors",
                      viral.music.trackId === m.id
                        ? "border-foreground bg-accent font-semibold"
                        : "hover:bg-accent/40",
                    )}
                  >
                    <div>
                      <div>{m.label}</div>
                      <div className="text-[11px] font-normal text-muted-foreground">{m.desc}</div>
                    </div>
                    {viral.music.trackId === m.id && <Check className="size-4 shrink-0" />}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Subtextos Cômicos ("Ele não sabia") & Linha do Tempo de Efeitos Ativos */}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-3 rounded-2xl border bg-card p-4">
              <h3 className="flex items-center gap-2 text-sm font-bold">
                <MessageSquarePlus className="size-4 text-amber-400" />
                Legendas & Subtextos que Contam a Piada
              </h3>
              <div className="flex gap-1.5">
                {(
                  [
                    { id: "thought", label: "💭 Pensamento" },
                    { id: "tracker", label: "🎯 Fixar / Emoji" },
                    { id: "censor_bar", label: "🤬 Tarja + Bipe" },
                  ] as const
                ).map((v) => (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => {
                      setSubtextVariant(v.id);
                      if (v.id === "censor_bar") setSubtextInput("CENSURADO #$@%!");
                      else if (v.id === "thought") setSubtextInput("Ele não sabia...");
                      else setSubtextInput("👈 Olha esse cara");
                    }}
                    className={cn(
                      "rounded-lg border px-2.5 py-1 text-xs font-medium",
                      subtextVariant === v.id && "border-foreground bg-accent",
                    )}
                  >
                    {v.label}
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <Input
                  value={subtextInput}
                  onChange={(e) => setSubtextInput(e.target.value)}
                  placeholder="Ex: Ele não sabia..."
                  className="text-xs"
                />
                <Button size="sm" onClick={addSubtextAtPlayhead}>
                  Inserir ({timecode(time)})
                </Button>
              </div>
            </div>

            {/* Tudo que foi aplicado no vídeo (Efeitos, Música, Textos, Layout e Cortes) */}
            <div className="space-y-3 rounded-2xl border bg-card p-4" data-testid="applied-effects-box">
              {(() => {
                const activeMusicItem = MUSIC_ITEMS.find((m) => m.id === viral.music.trackId);
                const hasMusic = viral.music.trackId !== "none";
                const totalAppliedCount =
                  viral.timeEffects.length +
                  viral.cameraKeyframes.length +
                  viral.sfx.length +
                  viral.subtexts.length +
                  viral.voiceEffects.length +
                  (hasMusic ? 1 : 0);
                const aspectLabel =
                  doc.framing.aspect === "square"
                    ? "1:1 Quadrado"
                    : doc.framing.aspect === "original"
                      ? "16:9 Original"
                      : "9:16 Vertical";
                const layoutLabel =
                  viral.layoutMode === "split"
                    ? "Split (Câmera + Jogo)"
                    : viral.layoutMode === "fullscreen_cam"
                      ? "Facecam Tela Cheia"
                      : viral.layoutMode === "pip"
                        ? "Câmera no Canto (PiP)"
                        : "Jogo Centralizado";
                const hasCuts =
                  doc.segments.length > 1 ||
                  (doc.segments[0] &&
                    (doc.segments[0].start > 0.05 ||
                      Math.abs(doc.segments[0].end - asset.duration) > 0.1));

                return (
                  <>
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="text-sm font-bold">
                        Tudo que foi aplicado{" "}
                        <span className="font-normal text-muted-foreground" data-testid="applied-effects-count">
                          ({totalAppliedCount})
                        </span>
                      </h3>
                      {totalAppliedCount > 0 && (
                        <button
                          type="button"
                          onClick={clearAllAppliedEffects}
                          data-testid="clear-all-effects"
                          className="rounded-md px-2 py-0.5 text-xs font-medium text-destructive hover:bg-destructive/10"
                        >
                          Limpar tudo
                        </button>
                      )}
                    </div>

                    {/* Estado atual: Formato, Layout, Legenda e Cortes */}
                    <div className="flex flex-wrap gap-1.5 text-[11px]" data-testid="applied-summary-badges">
                      <Badge variant="secondary" className="font-medium">
                        📐 {aspectLabel}
                      </Badge>
                      <Badge variant="secondary" className="font-medium">
                        🎬 {layoutLabel}
                      </Badge>
                      <Badge
                        variant={doc.captions.enabled ? "secondary" : "outline"}
                        className="font-medium"
                      >
                        {doc.captions.enabled ? "💬 Legenda Ativa" : "🚫 Sem Legenda"}
                      </Badge>
                      {hasCuts && (
                        <Badge variant="secondary" className="font-medium">
                          ✂️ {doc.segments.length} {doc.segments.length === 1 ? "parte" : "partes"} ({timecode(editDuration(doc))})
                        </Badge>
                      )}
                    </div>

                    <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1 text-xs" data-testid="applied-effects-list">
                      {hasMusic && (
                        <div
                          className="flex items-center justify-between rounded-lg border border-purple-500/30 bg-purple-500/10 px-2.5 py-1.5"
                          data-testid="applied-music-item"
                        >
                          <span className="font-medium">
                            🎵 Música: {activeMusicItem?.label ?? viral.music.trackId}
                            {viral.music.autoDuck ? " • Auto-Ducking" : ""}
                          </span>
                          <button
                            type="button"
                            aria-label="Remover música de fundo"
                            data-testid="remove-applied-music"
                            onClick={() => setMusicTrack("none")}
                            className="text-muted-foreground hover:text-destructive"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                      )}

                      {viral.timeEffects.map((te) => (
                        <div key={te.id} className="flex items-center justify-between rounded-lg border bg-muted/30 px-2.5 py-1.5">
                          <button type="button" onClick={() => seek(te.start)} className="text-left font-medium hover:underline">
                            💀 [{timecode(te.start)}] {te.reason || te.kind}
                          </button>
                          <button
                            type="button"
                            aria-label={`Remover ${te.reason || te.kind}`}
                            onClick={() =>
                              editor.update((c) => {
                                const v = ensureViralConfig(c);
                                return { ...c, viral: { ...v, timeEffects: v.timeEffects.filter((x) => x.id !== te.id) } };
                              })
                            }
                            className="text-muted-foreground hover:text-destructive"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                      ))}

                      {viral.cameraKeyframes.map((kf) => (
                        <div key={kf.id} className="flex items-center justify-between rounded-lg border bg-muted/30 px-2.5 py-1.5">
                          <button type="button" onClick={() => seek(kf.start)} className="text-left font-medium hover:underline">
                            🎥 [{timecode(kf.start)}] {kf.mode.toUpperCase()} — {kf.reason || "Câmera"}
                          </button>
                          <button
                            type="button"
                            aria-label={`Remover ${kf.mode}`}
                            onClick={() =>
                              editor.update((c) => {
                                const v = ensureViralConfig(c);
                                return { ...c, viral: { ...v, cameraKeyframes: v.cameraKeyframes.filter((x) => x.id !== kf.id) } };
                              })
                            }
                            className="text-muted-foreground hover:text-destructive"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                      ))}

                      {viral.subtexts.map((st) => (
                        <div key={st.id} className="flex items-center justify-between rounded-lg border bg-muted/30 px-2.5 py-1.5">
                          <button type="button" onClick={() => seek(st.start)} className="text-left font-medium hover:underline">
                            💭 [{timecode(st.start)}] &ldquo;{st.text}&rdquo;
                          </button>
                          <button
                            type="button"
                            aria-label={`Remover texto ${st.text}`}
                            onClick={() =>
                              editor.update((c) => {
                                const v = ensureViralConfig(c);
                                return { ...c, viral: { ...v, subtexts: v.subtexts.filter((x) => x.id !== st.id) } };
                              })
                            }
                            className="text-muted-foreground hover:text-destructive"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                      ))}

                      {viral.sfx.map((s) => {
                        const sfxMeta = SFX_ITEMS.find((item) => item.id === s.sfxId);
                        return (
                          <div key={s.id} className="flex items-center justify-between rounded-lg border bg-muted/30 px-2.5 py-1.5">
                            <button type="button" onClick={() => seek(s.start)} className="text-left font-medium hover:underline">
                              🔊 [{timecode(s.start)}] SFX: {sfxMeta ? `${sfxMeta.emoji} ${sfxMeta.label}` : s.sfxId}
                            </button>
                            <button
                              type="button"
                              aria-label={`Remover SFX ${s.sfxId}`}
                              onClick={() =>
                                editor.update((c) => {
                                  const v = ensureViralConfig(c);
                                  return { ...c, viral: { ...v, sfx: v.sfx.filter((x) => x.id !== s.id) } };
                                })
                              }
                              className="text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 className="size-3.5" />
                            </button>
                          </div>
                        );
                      })}

                      {viral.voiceEffects.map((ve) => (
                        <div key={ve.id} className="flex items-center justify-between rounded-lg border bg-muted/30 px-2.5 py-1.5">
                          <button type="button" onClick={() => seek(ve.start)} className="text-left font-medium hover:underline">
                            🎙️ [{timecode(ve.start)}] Voz: {ve.effect === "bass_boost" ? "Grave Estourado" : "Reverb"}
                          </button>
                          <button
                            type="button"
                            aria-label={`Remover efeito de voz ${ve.effect}`}
                            onClick={() =>
                              editor.update((c) => {
                                const v = ensureViralConfig(c);
                                return { ...c, viral: { ...v, voiceEffects: v.voiceEffects.filter((x) => x.id !== ve.id) } };
                              })
                            }
                            className="text-muted-foreground hover:text-destructive"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                      ))}

                      {totalAppliedCount === 0 && (
                        <p className="py-4 text-center text-muted-foreground">
                          Nenhum efeito ou som aplicado ainda. Use os presets acima ou a Direção Viral IA!
                        </p>
                      )}
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
