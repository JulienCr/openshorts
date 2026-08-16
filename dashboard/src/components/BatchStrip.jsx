import React, { useState, useEffect, useMemo } from 'react';
import { ChevronDown, ChevronRight, X, Loader2 } from 'lucide-react';
import { apiJson } from '../lib/api';

const TERMINAL = new Set(['completed', 'failed']);

/**
 * Progress of a multi-file submission, above the single-job view.
 *
 * Fetches its own state like HistoryTab does, which keeps App.jsx to a handful of
 * lines. One request covers the whole batch — /api/jobs reads the in-memory job
 * records and touches no disk, so polling thirty jobs costs about as much as
 * polling one, where thirty calls to /api/status would not.
 */
export default function BatchStrip({ batch, activeJobId, onOpen, onDismiss }) {
    const [live, setLive] = useState({});
    const [open, setOpen] = useState(false);

    const ids = useMemo(() => (batch?.jobs || []).map((j) => j.job_id), [batch]);

    useEffect(() => {
        if (!batch?.id) return undefined;
        let cancelled = false;
        let timer;

        const tick = async () => {
            try {
                const d = await apiJson(`/api/jobs?batch_id=${encodeURIComponent(batch.id)}`);
                if (cancelled) return;
                const byId = {};
                (d.jobs || []).forEach((j) => { byId[j.job_id] = j; });
                setLive(byId);
                // Stop once nothing can change again. A finished batch that keeps
                // polling all night is the kind of thing nobody notices and
                // everybody pays for.
                const settled = ids.length > 0 && ids.every(
                    (id) => !byId[id] || TERMINAL.has(byId[id].status));
                if (!settled) timer = setTimeout(tick, 3000);
            } catch (_) {
                if (!cancelled) timer = setTimeout(tick, 10000);
            }
        };
        tick();
        return () => { cancelled = true; clearTimeout(timer); };
    }, [batch?.id, ids]);

    if (!batch?.jobs?.length) return null;

    const rows = batch.jobs.map((j) => ({ ...j, ...(live[j.job_id] || {}) }));
    const done = rows.filter((r) => r.status === 'completed').length;
    const failed = rows.filter((r) => r.status === 'failed').length;
    const running = rows.filter((r) => r.status === 'processing').length;
    const allSettled = rows.every((r) => TERMINAL.has(r.status));

    const badge = (s) => (s === 'completed' ? 'badge-ok'
        : s === 'failed' ? 'badge-danger'
            : s === 'processing' ? 'badge-brass' : 'badge-warn');

    return (
        <div className="card p-3 sm:p-4 mb-4 animate-fade">
            <div className="flex items-center gap-3">
                <button
                    type="button"
                    onClick={() => setOpen((v) => !v)}
                    className="flex items-center gap-2 flex-1 min-w-0 text-left text-sm text-ink2 hover:text-ink transition-colors"
                >
                    {open ? <ChevronDown size={16} className="shrink-0" /> : <ChevronRight size={16} className="shrink-0" />}
                    {running > 0 && <Loader2 size={14} className="animate-spin text-brass shrink-0" />}
                    <span className="truncate">
                        {done}/{rows.length} done
                        {running > 0 && ` · ${running} running`}
                        {failed > 0 && ` · ${failed} failed`}
                    </span>
                </button>
                {/* Only offered once nothing is left to watch: dismissing mid-run
                    would drop the only handle on the other jobs. */}
                {allSettled && (
                    <button
                        type="button"
                        onClick={onDismiss}
                        aria-label="Dismiss this batch"
                        className="p-1 text-muted hover:text-ink hover:bg-paper3 rounded-full transition-colors"
                    >
                        <X size={16} />
                    </button>
                )}
            </div>

            {open && (
                <div className="mt-3 max-h-64 overflow-y-auto divide-y divide-rule">
                    {rows.map((r) => (
                        <button
                            key={r.job_id}
                            type="button"
                            onClick={() => onOpen(r.job_id)}
                            className={`w-full flex items-center gap-3 px-2 py-2 text-left transition-colors
                                ${r.job_id === activeJobId ? 'bg-paper3' : 'hover:bg-paper3'}`}
                        >
                            <span className={`${badge(r.status)} shrink-0`}>{r.status || 'queued'}</span>
                            <span className="flex-1 min-w-0">
                                <span className="block truncate text-sm text-ink2">{r.name}</span>
                                {r.last_log && (
                                    <span className="block truncate readout">{r.last_log}</span>
                                )}
                            </span>
                            {r.clip_count > 0 && (
                                <span className="shrink-0 font-mono text-xs text-muted">
                                    {r.clip_count} clip{r.clip_count === 1 ? '' : 's'}
                                </span>
                            )}
                        </button>
                    ))}
                    {batch.skipped?.length > 0 && (
                        <p className="px-2 py-2 readout text-brass">
                            {batch.skipped.length} file{batch.skipped.length === 1 ? '' : 's'} skipped —
                            gone from the server folder since the list was read.
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
