"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ExternalLink,
  HardDrive,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  Youtube,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/app/app-shell";
import { PageLoading } from "@/components/app/page-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSession } from "@/lib/auth-client";
import { cn } from "@/lib/utils";

interface CachedVideoItem {
  id: string;
  kind: "youtube" | "upload";
  key: string;
  title: string;
  source_url?: string | null;
  size_bytes: number;
  file_count: number;
  updated_at?: string | null;
  in_use: boolean;
  task_ids: string[];
}

interface CachedVideosResponse {
  total_cache_bytes: number;
  youtube_cache_bytes: number;
  upload_cache_bytes: number;
  clips_storage_bytes: number;
  editor_storage_bytes: number;
  youtube_count: number;
  upload_count: number;
  items: CachedVideoItem[];
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "0 MB";
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(2).replace(".", ",")} GB`;
  const mb = bytes / (1024 * 1024);
  if (mb >= 0.1) return `${mb.toFixed(1).replace(".", ",")} MB`;
  const kb = bytes / 1024;
  return `${Math.max(1, Math.round(kb))} KB`;
}

function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return "—";
  }
}

export default function VideoCachePage() {
  const { data: session, isPending } = useSession();
  const [data, setData] = useState<CachedVideosResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [clearingAll, setClearingAll] = useState(false);
  const [confirmClearAll, setConfirmClearAll] = useState(false);
  const [search, setSearch] = useState("");
  const [filterKind, setFilterKind] = useState<"all" | "youtube" | "upload">("all");

  const loadCache = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/tasks/cache/videos", { cache: "no-store" });
      if (!res.ok) throw new Error("Falha ao carregar vídeos em cache.");
      const payload: CachedVideosResponse = await res.json();
      setData(payload);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao carregar cache.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!session?.user?.id) {
      setLoading(false);
      return;
    }
    void loadCache();
  }, [loadCache, session?.user?.id]);

  const handleDeleteOne = useCallback(
    async (item: CachedVideoItem) => {
      if (item.in_use) {
        toast.info("Esse vídeo está em processamento no momento.");
        return;
      }
      setDeletingId(item.id);
      try {
        const res = await fetch(`/api/tasks/cache/videos/${encodeURIComponent(item.id)}`, {
          method: "DELETE",
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(body?.detail || "Não foi possível excluir o vídeo do cache.");
        }
        toast.success(`"${item.title}" excluído (${formatBytes(body.freed_bytes ?? item.size_bytes)} liberados)!`);
        await loadCache();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Erro ao excluir do cache.");
      } finally {
        setDeletingId(null);
      }
    },
    [loadCache],
  );

  const handleClearAll = useCallback(async () => {
    setClearingAll(true);
    setConfirmClearAll(false);
    try {
      const res = await fetch("/api/tasks/cache/videos/clear", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body?.detail || "Falha ao limpar cache.");
      }
      toast.success(
        `${body.deleted_count ?? 0} vídeos removidos — ${formatBytes(body.freed_bytes ?? 0)} liberados!`,
      );
      await loadCache();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao limpar cache.");
    } finally {
      setClearingAll(false);
    }
  }, [loadCache]);

  const filteredItems = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.items.filter((item) => {
      if (filterKind !== "all" && item.kind !== filterKind) return false;
      if (!q) return true;
      return (
        item.title.toLowerCase().includes(q) ||
        item.key.toLowerCase().includes(q)
      );
    });
  }, [data, filterKind, search]);

  const removableCount = useMemo(
    () => (data?.items ?? []).filter((item) => !item.in_use).length,
    [data?.items],
  );

  if (isPending || (loading && !data)) {
    return (
      <AppShell>
        <PageLoading />
      </AppShell>
    );
  }

  const ytRatio =
    data && data.total_cache_bytes > 0
      ? Math.round((data.youtube_cache_bytes / data.total_cache_bytes) * 100)
      : 0;

  return (
    <AppShell>
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 font-display text-2xl font-bold tracking-tight">
              <HardDrive className="size-6 text-sky-500" />
              Vídeos em Cache
            </h1>
            <p className="text-sm text-muted-foreground">
              Gerencie os vídeos baixados e enviados que ficam salvos no servidor para estender cortes e editar. Exclua os que não precisa mais para liberar espaço em disco.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void loadCache()}
              disabled={loading}
              data-testid="cache-refresh-btn"
              className="gap-1.5"
            >
              <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
              Atualizar
            </Button>

            {removableCount > 0 &&
              (confirmClearAll ? (
                <div className="flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-2.5 py-1">
                  <span className="text-xs font-medium text-destructive">
                    Apagar {removableCount} vídeos?
                  </span>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    className="h-7 px-2.5 text-xs"
                    disabled={clearingAll}
                    onClick={() => void handleClearAll()}
                    data-testid="cache-confirm-clear-all-btn"
                  >
                    {clearingAll ? <Loader2 className="size-3.5 animate-spin" /> : "Confirmar"}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    onClick={() => setConfirmClearAll(false)}
                  >
                    Cancelar
                  </Button>
                </div>
              ) : (
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={() => setConfirmClearAll(true)}
                  disabled={clearingAll}
                  data-testid="cache-clear-all-btn"
                  className="gap-1.5"
                >
                  <Trash2 className="size-3.5" />
                  Limpar Todos ({removableCount})
                </Button>
              ))}
          </div>
        </div>

        {/* Storage Summary Cards */}
        {data && (
          <div className="grid gap-4 sm:grid-cols-3" data-testid="cache-summary-cards">
            <div className="space-y-2 rounded-2xl border bg-card p-4 shadow-xs">
              <div className="text-xs font-medium text-muted-foreground">
                Espaço em Cache de Vídeos
              </div>
              <div className="text-2xl font-bold tabular-nums" data-testid="cache-total-size">
                {formatBytes(data.total_cache_bytes)}
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full bg-rose-500 transition-all"
                  style={{ width: `${ytRatio}%` }}
                />
              </div>
              <div className="flex justify-between text-[11px] text-muted-foreground">
                <span>YouTube: {formatBytes(data.youtube_cache_bytes)}</span>
                <span>Uploads: {formatBytes(data.upload_cache_bytes)}</span>
              </div>
            </div>

            <div className="space-y-1.5 rounded-2xl border bg-card p-4 shadow-xs">
              <div className="text-xs font-medium text-muted-foreground">
                Vídeos Armazenados
              </div>
              <div className="text-2xl font-bold tabular-nums" data-testid="cache-total-count">
                {data.items.length}
              </div>
              <div className="text-xs text-muted-foreground">
                {data.youtube_count} do YouTube • {data.upload_count} enviados por arquivo
              </div>
            </div>

            <div className="space-y-1.5 rounded-2xl border bg-card p-4 shadow-xs">
              <div className="text-xs font-medium text-muted-foreground">
                Clipes Gerados &amp; Rascunhos do Editor
              </div>
              <div className="text-2xl font-bold tabular-nums">
                {formatBytes(data.clips_storage_bytes + data.editor_storage_bytes)}
              </div>
              <div className="text-xs text-muted-foreground">
                Clipes: {formatBytes(data.clips_storage_bytes)} • Editor: {formatBytes(data.editor_storage_bytes)}
              </div>
            </div>
          </div>
        )}

        {/* Search & Filter */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="relative w-full max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar por título ou ID do vídeo..."
              className="pl-9"
              data-testid="cache-search-input"
            />
          </div>

          <div className="inline-flex rounded-xl border bg-muted/40 p-1 text-xs">
            {(
              [
                { id: "all", label: `Todos (${data?.items.length ?? 0})` },
                { id: "youtube", label: `YouTube (${data?.youtube_count ?? 0})` },
                { id: "upload", label: `Uploads (${data?.upload_count ?? 0})` },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setFilterKind(tab.id)}
                data-testid={`cache-filter-${tab.id}`}
                className={cn(
                  "rounded-lg px-3 py-1 font-medium transition-colors",
                  filterKind === tab.id
                    ? "bg-background text-foreground shadow-xs"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Video List */}
        <div className="space-y-2.5" data-testid="cache-items-list">
          {filteredItems.length === 0 ? (
            <div className="rounded-2xl border bg-card p-10 text-center text-sm text-muted-foreground">
              Nenhum vídeo em cache encontrado.
            </div>
          ) : (
            filteredItems.map((item) => (
              <div
                key={item.id}
                data-testid={`cache-item-${item.id}`}
                className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border bg-card p-4 shadow-xs transition-colors hover:bg-accent/20"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      variant="secondary"
                      className={cn(
                        "gap-1 text-[11px]",
                        item.kind === "youtube"
                          ? "bg-rose-500/10 text-rose-600 dark:text-rose-400"
                          : "bg-sky-500/10 text-sky-600 dark:text-sky-400",
                      )}
                    >
                      {item.kind === "youtube" ? (
                        <Youtube className="size-3" />
                      ) : (
                        <Upload className="size-3" />
                      )}
                      {item.kind === "youtube" ? "YouTube" : "Upload"}
                    </Badge>

                    <span className="truncate font-semibold">{item.title}</span>

                    {item.in_use && (
                      <Badge variant="outline" className="border-amber-500/50 text-[11px] text-amber-600">
                        Em processamento
                      </Badge>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span className="font-mono">{item.key}</span>
                    <span>•</span>
                    <span>{item.file_count} {item.file_count === 1 ? "arquivo" : "arquivos"}</span>
                    <span>•</span>
                    <span>Atualizado em {formatDate(item.updated_at)}</span>
                    {item.task_ids[0] && (
                      <>
                        <span>•</span>
                        <Link
                          href={`/tasks/${item.task_ids[0]}`}
                          className="inline-flex items-center gap-1 text-foreground hover:underline"
                        >
                          Ver cortes <ExternalLink className="size-3" />
                        </Link>
                      </>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <div className="text-base font-bold tabular-nums">{formatBytes(item.size_bytes)}</div>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={item.in_use || deletingId === item.id}
                    onClick={() => void handleDeleteOne(item)}
                    data-testid={`cache-delete-${item.id}`}
                    className="gap-1.5 text-destructive hover:bg-destructive/10 hover:text-destructive"
                  >
                    {deletingId === item.id ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="size-3.5" />
                    )}
                    Excluir
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      </main>
    </AppShell>
  );
}
