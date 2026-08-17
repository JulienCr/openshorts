import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link2, Upload, FileVideo, X, Info, Loader2, HardDrive, RefreshCw } from 'lucide-react';
import { getApiUrl } from '../config';

const SUPPORTED_PLATFORMS = [
    'YouTube', 'Vimeo', 'TikTok', 'X / Twitter', 'Twitch',
    'Facebook', 'Instagram', 'Dailymotion', 'Reddit', 'Streamable',
];

export default function MediaInput({ onProcess, isProcessing }) {
    const [youtubeUrlEnabled, setYoutubeUrlEnabled] = useState(true);
    // Off unless the server sets LOCAL_INGEST_DIR (self-host only), so the tab
    // never shows up on a deployment that would just answer 403.
    const [localIngestEnabled, setLocalIngestEnabled] = useState(false);
    const [localFiles, setLocalFiles] = useState([]);
    const [localTruncated, setLocalTruncated] = useState(false);
    // A Set of relative names. Several files go out as one batch; a single one
    // takes the exact path it always did, so the common case is untouched.
    const [localSelected, setLocalSelected] = useState(() => new Set());
    const [localFilter, setLocalFilter] = useState('');
    const [localSources, setLocalSources] = useState([]);
    const [localError, setLocalError] = useState('');
    const [loadingLocal, setLoadingLocal] = useState(false);
    // File upload is the primary path; the link is secondary.
    const [mode, setMode] = useState('file'); // 'file' | 'url' | 'local'
    const [url, setUrl] = useState('');
    const [file, setFile] = useState(null);
    const [acknowledged, setAcknowledged] = useState(false);
    // Cloud only: the attestation is the record of consent the terms promise.
    // Self-hosting, you process your own files, so the box measures nothing and
    // is one click per job. Defaults to true so a failed /api/config keeps
    // today's behaviour instead of dropping the guard silently.
    const [requireRights, setRequireRights] = useState(true);
    // null = inherit the server's default style (Settings > Default style).
    // It has to be the initial state, not just an option: a concrete value here
    // is sent on every submission and always beats the preset, which made the
    // panel's format control do nothing for dashboard jobs.
    const [outputFormat, setOutputFormat] = useState(null); // null | vertical | horizontal | square
    // On by default: the automatic framing is the thing most likely to need a
    // second opinion, and finding out after the render costs a whole re-run.
    const [safeVariant, setSafeVariant] = useState(true);
    // Channel branding. The checkbox only appears when the server has a brand
    // asset to burn (brandingAvailable); its initial state mirrors the operator's
    // BRAND_WATERMARK default, so unticking it is an override for this job only.
    const [brandingAvailable, setBrandingAvailable] = useState(false);
    const [branding, setBranding] = useState(false);
    const [showInfo, setShowInfo] = useState(false);
    const infoRef = useRef(null);

    // Close the compatibility popover on any outside click.
    useEffect(() => {
        if (!showInfo) return;
        const onClick = (e) => {
            if (infoRef.current && !infoRef.current.contains(e.target)) setShowInfo(false);
        };
        document.addEventListener('mousedown', onClick);
        return () => document.removeEventListener('mousedown', onClick);
    }, [showInfo]);

    useEffect(() => {
        fetch(getApiUrl('/api/config'))
            .then((r) => r.ok ? r.json() : null)
            .then((cfg) => {
                if (cfg && cfg.youtubeUrlEnabled === false) {
                    setYoutubeUrlEnabled(false);
                    setMode('file');
                }
                if (cfg && cfg.localIngestEnabled) setLocalIngestEnabled(true);
                // `=== false`, not `!cfg.billingEnabled`: a server too old to
                // send the key keeps the box rather than losing it by accident.
                if (cfg && cfg.billingEnabled === false) setRequireRights(false);
                if (cfg && cfg.brandingAvailable) {
                    setBrandingAvailable(true);
                    setBranding(!!cfg.brandingDefault);
                }
            })
            .catch(() => {});
    }, []);

    const loadLocalFiles = useCallback(() => {
        setLoadingLocal(true);
        setLocalError('');
        fetch(getApiUrl('/api/local-files'))
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error('unreachable'))))
            .then((d) => {
                setLocalFiles(d.files || []);
                setLocalTruncated(!!d.truncated);
                setLocalSources(d.sources || []);
            })
            .catch(() => setLocalError("Could not read the server's video folder."))
            .finally(() => setLoadingLocal(false));
    }, []);

    // Re-read on every entry into the tab, not once on mount: the whole point
    // of this path is to drop a file on the server and process it, so a list
    // fetched at page load is stale exactly when it matters. The refresh button
    // covers dropping a file while the tab is already open.
    useEffect(() => {
        if (localIngestEnabled && mode === 'local') loadLocalFiles();
    }, [localIngestEnabled, mode, loadLocalFiles]);

    // A link pasted in the landing hero: preload it here so the user picks up
    // where they left off. Never auto-submitted — the user still presses the
    // button (and, where the attestation is shown, ticks it first).
    useEffect(() => {
        let pending = null;
        try {
            pending = localStorage.getItem('os_pending_url');
            if (pending) localStorage.removeItem('os_pending_url');
        } catch { /* ignore */ }
        if (pending) {
            setMode('url');
            setUrl(pending);
        }
    }, []);

    // Nothing to attest to when the box is not shown.
    const rightsOk = !requireRights || acknowledged;

    const formatSize = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);

    const needle = localFilter.trim().toLowerCase();
    const visibleFiles = needle
        ? localFiles.filter((f) => f.name.toLowerCase().includes(needle))
        : localFiles;
    const selectedSizeMb = localFiles.reduce(
        (sum, f) => (localSelected.has(f.name) ? sum + f.size_mb : sum), 0);
    // A folder the server could read but that holds nothing at all.
    const emptySources = localSources.filter((s) => s.entries === 0);

    const toggleLocal = (name) => setLocalSelected((prev) => {
        const next = new Set(prev);
        if (!next.delete(name)) next.add(name);
        return next;
    });

    const selectAll = (on) => setLocalSelected((prev) => {
        const next = new Set(prev);
        // Only ever touches what the filter is showing, so clearing after a
        // search cannot silently drop a selection made under another one.
        visibleFiles.forEach((f) => (on ? next.add(f.name) : next.delete(f.name)));
        return next;
    });

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!rightsOk) return;
        const variants = safeVariant ? 'auto,safe' : 'auto';
        // Only sent when the server offered the choice: on a server with no
        // brand asset, `undefined` leaves the decision to BRAND_WATERMARK
        // instead of pinning it to this build's idea of the default.
        const brand = brandingAvailable ? branding : undefined;
        const common = { acknowledged: true, outputFormat, variants, branding: brand };
        if (mode === 'url' && url) {
            onProcess({ type: 'url', payload: url, ...common });
        } else if (mode === 'file' && file) {
            onProcess({ type: 'file', payload: file, ...common });
        } else if (mode === 'local' && localSelected.size) {
            const names = [...localSelected];
            // One file takes the single-job path it always took, byte for byte.
            // The batch endpoint only comes into play from two, so the common
            // case gains nothing to go wrong.
            onProcess(names.length === 1
                ? { type: 'local', payload: names[0], ...common }
                : { type: 'local-batch', payload: names, ...common });
        }
    };

    const handleDrop = (e) => {
        e.preventDefault();
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            setFile(e.dataTransfer.files[0]);
            setMode('file');
        }
    };

    return (
        <div className="card p-4 sm:p-6 animate-fade">
            <div className="flex gap-4 sm:gap-6 mb-6 border-b border-rule">
                <button
                    onClick={() => setMode('file')}
                    className={`flex items-center gap-2 pb-3 px-1 -mb-px border-b-2 text-sm lowercase whitespace-nowrap transition-colors ${mode === 'file'
                        ? 'text-ink border-brass'
                        : 'text-muted border-transparent hover:text-ink2'
                        }`}
                >
                    <Upload size={16} className={`hidden sm:block ${mode === 'file' ? 'text-brass' : ''}`} />
                    Upload File
                </button>
                {youtubeUrlEnabled && (
                    <button
                        onClick={() => setMode('url')}
                        className={`flex items-center gap-2 pb-3 px-1 -mb-px border-b-2 text-sm lowercase whitespace-nowrap transition-colors ${mode === 'url'
                            ? 'text-ink border-brass'
                            : 'text-muted border-transparent hover:text-ink2'
                            }`}
                    >
                        <Link2 size={16} className={`hidden sm:block ${mode === 'url' ? 'text-brass' : ''}`} />
                        Video URL
                    </button>
                )}
                {localIngestEnabled && (
                    <button
                        onClick={() => setMode('local')}
                        className={`flex items-center gap-2 pb-3 px-1 -mb-px border-b-2 text-sm lowercase whitespace-nowrap transition-colors ${mode === 'local'
                            ? 'text-ink border-brass'
                            : 'text-muted border-transparent hover:text-ink2'
                            }`}
                    >
                        <HardDrive size={16} className={`hidden sm:block ${mode === 'local' ? 'text-brass' : ''}`} />
                        On Server
                    </button>
                )}
            </div>

            <form onSubmit={handleSubmit}>
                {mode === 'url' ? (
                    <div className="space-y-4">
                        <div className="relative">
                            <input
                                type="url"
                                value={url}
                                onChange={(e) => setUrl(e.target.value)}
                                placeholder="https://... paste a video link"
                                className="input-field pr-11"
                                required
                            />
                            <div className="absolute inset-y-0 right-2 flex items-center" ref={infoRef}>
                                <button
                                    type="button"
                                    onClick={() => setShowInfo((v) => !v)}
                                    aria-label="Supported platforms"
                                    className="p-1.5 text-muted hover:text-brass transition-colors"
                                >
                                    <Info size={16} />
                                </button>
                                {showInfo && (
                                    <div className="absolute right-0 top-full mt-2 w-64 z-20 card p-4 text-left animate-fade">
                                        <p className="eyebrow mb-2">Paste a link from</p>
                                        <div className="flex flex-wrap gap-1.5">
                                            {SUPPORTED_PLATFORMS.map((p) => (
                                                <span key={p} className="text-xs px-2 py-0.5 rounded-full bg-paper3 text-ink2">
                                                    {p}
                                                </span>
                                            ))}
                                        </div>
                                        <p className="text-xs text-muted mt-2.5 leading-relaxed">
                                            …and 1,000+ more sites. If a link has a public video, we can usually fetch it.
                                        </p>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                ) : mode === 'local' ? (
                    <div className="space-y-3">
                        {/* Filter and refresh stay mounted whatever the folder
                            holds — hiding them on the empty state would strand
                            the user right after dropping their first file. */}
                        <div className="flex items-center gap-2">
                            <input
                                type="search"
                                value={localFilter}
                                onChange={(e) => setLocalFilter(e.target.value)}
                                placeholder={localFiles.length ? 'Filter by name…' : 'Nothing to pick yet'}
                                className="input-field flex-1"
                                disabled={loadingLocal || localFiles.length === 0}
                            />
                            <button
                                type="button"
                                onClick={loadLocalFiles}
                                disabled={loadingLocal}
                                title="Re-read the server folder"
                                aria-label="Re-read the server folder"
                                className="p-2.5 rounded-input border border-rule2 text-muted hover:text-ink hover:border-brass disabled:opacity-50 transition-colors"
                            >
                                <RefreshCw size={16} className={loadingLocal ? 'animate-spin' : ''} />
                            </button>
                        </div>

                        {/* A source folder with nothing at all in it is nearly
                            always a mount that did not happen, not an empty
                            folder — and as a short file list it looks like
                            neither. Say so instead of letting it read as "there
                            is nothing there". */}
                        {emptySources.map((s) => (
                            <p key={s.name} className="readout text-brass">
                                {s.name}/ is empty ({s.fstype || 'unknown filesystem'}) — if it should be
                                a mounted drive, the mount is gone.
                            </p>
                        ))}

                        {loadingLocal ? (
                            <p className="readout">Reading the server folder…</p>
                        ) : localError ? (
                            <p className="text-sm text-muted">{localError}</p>
                        ) : localFiles.length === 0 ? (
                            <p className="text-sm text-muted">
                                No video files in the server folder yet — drop one in and hit refresh.
                            </p>
                        ) : (
                            <>
                                <div className="max-h-64 overflow-y-auto rounded-input border border-rule2 divide-y divide-rule">
                                    {visibleFiles.map((f) => (
                                        <label
                                            key={f.name}
                                            className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-paper3 transition-colors"
                                        >
                                            <input
                                                type="checkbox"
                                                checked={localSelected.has(f.name)}
                                                onChange={() => toggleLocal(f.name)}
                                                className="accent-[var(--color-accent)] cursor-pointer shrink-0"
                                            />
                                            <span className="flex-1 min-w-0 truncate text-sm text-ink2">{f.name}</span>
                                            <span className="shrink-0 font-mono text-xs text-muted">{formatSize(f.size_mb)}</span>
                                        </label>
                                    ))}
                                    {visibleFiles.length === 0 && (
                                        <p className="px-3 py-2 text-sm text-muted">No file matches “{localFilter}”.</p>
                                    )}
                                </div>

                                <div className="flex items-center justify-between gap-3 text-xs">
                                    <div className="flex gap-3">
                                        {/* Acting on the filtered set, not on all
                                            500: "all" after a filter means the
                                            files you are looking at. */}
                                        <button type="button" onClick={() => selectAll(true)}
                                            className="text-muted hover:text-brass transition-colors lowercase">
                                            select all{localFilter && ' shown'}
                                        </button>
                                        <button type="button" onClick={() => selectAll(false)}
                                            className="text-muted hover:text-brass transition-colors lowercase">
                                            clear
                                        </button>
                                    </div>
                                    <span className="text-muted">
                                        {localSelected.size
                                            ? `${localSelected.size} selected · ${formatSize(selectedSizeMb)}`
                                            : `${visibleFiles.length} file${visibleFiles.length === 1 ? '' : 's'}`}
                                    </span>
                                </div>

                                <p className="readout">
                                    Read straight from the server's disk — nothing is uploaded, so the size limit does not apply.
                                </p>
                                {localSelected.size > 1 && (
                                    <p className="readout text-brass">
                                        Queued and rendered one at a time, not in parallel — plan for
                                        roughly {localSelected.size}× a single run.
                                    </p>
                                )}
                                {localTruncated && (
                                    <p className="readout text-brass">
                                        Only the first {localFiles.length} files are listed — narrow the server folder to see the rest.
                                    </p>
                                )}
                            </>
                        )}
                    </div>
                ) : (
                    <div
                        className={`border-2 border-dashed rounded-card p-6 sm:p-8 text-center transition-colors ${file ? 'border-brass' : 'border-rule2 hover:border-brass'
                            }`}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={handleDrop}
                    >
                        {file ? (
                            <div className="flex items-center justify-center gap-3 text-ok min-w-0">
                                <FileVideo size={18} className="shrink-0" />
                                <span className="font-medium truncate">{file.name}</span>
                                <button
                                    type="button"
                                    onClick={() => setFile(null)}
                                    className="p-1 text-muted hover:text-ink hover:bg-paper3 rounded-full transition-colors"
                                >
                                    <X size={16} />
                                </button>
                            </div>
                        ) : (
                            <label className="cursor-pointer block">
                                <input
                                    type="file"
                                    accept="video/*"
                                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                                    className="hidden"
                                />
                                <Upload className="mx-auto mb-3 text-muted" size={18} />
                                <p className="text-ink2 lowercase">Click to upload or drag and drop</p>
                                <p className="readout mt-2">MP4, MOV up to 2GB</p>
                            </label>
                        )}
                    </div>
                )}

                {/* Output format selector */}
                <div className="mt-5">
                    <p className="eyebrow mb-2">Output format</p>
                    <div className="grid grid-cols-4 gap-2">
                        {[
                            { value: null, label: 'default', hint: 'Your saved style', w: 22, h: 26, dashed: true },
                            { value: 'vertical', label: '9:16', hint: 'Shorts · Reels · TikTok', w: 18, h: 32 },
                            { value: 'square', label: '1:1', hint: 'Feed posts', w: 28, h: 28 },
                            { value: 'horizontal', label: '16:9', hint: 'Keep landscape · YouTube', w: 36, h: 20 },
                        ].map((f) => {
                            const active = outputFormat === f.value;
                            return (
                                <button
                                    key={f.value ?? 'default'}
                                    type="button"
                                    onClick={() => setOutputFormat(f.value)}
                                    className={`py-3 px-2 rounded-input border flex flex-col items-center gap-2 transition-colors
                                        ${active ? 'border-[color:var(--color-accent)] text-ink' : 'border-rule2 text-muted hover:border-[color:var(--color-accent)]'}`}
                                >
                                    {/* Aspect-ratio glyph */}
                                    <span
                                        className="rounded-[3px] border-2 transition-colors"
                                        style={{
                                            width: `${f.w}px`,
                                            height: `${f.h}px`,
                                            borderStyle: f.dashed ? 'dashed' : 'solid',
                                            borderColor: active ? 'var(--color-accent)' : 'var(--color-rule-2)',
                                            backgroundColor: active ? 'color-mix(in srgb, var(--color-accent) 22%, transparent)' : 'transparent',
                                        }}
                                    />
                                    <span className="block font-mono text-sm leading-none">{f.label}</span>
                                    <span className="block text-[10px] leading-tight text-center text-muted">{f.hint}</span>
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* Second render per clip. Hidden only when the user has
                    explicitly picked 16:9, where nothing is reframed and the
                    server collapses the request anyway. `null` means "inherit
                    the server's default style", which may well be vertical, so
                    it must NOT hide the box. */}
                {outputFormat !== 'horizontal' && (
                    <label className="flex items-start gap-2 mt-5 text-xs text-muted cursor-pointer select-none">
                        <input
                            type="checkbox"
                            checked={safeVariant}
                            onChange={(e) => setSafeVariant(e.target.checked)}
                            className="mt-0.5 accent-[var(--color-accent)] cursor-pointer"
                        />
                        <span>
                            Also render a <strong className="text-ink">safe</strong> version of every clip —
                            the whole frame on a blurred background, camera fixed, no subject tracking.
                            Gives you a fallback when the automatic framing gets it wrong.
                            <span className="block mt-0.5 opacity-70">Doubles this job's render time and disk usage.</span>
                        </span>
                    </label>
                )}

                {brandingAvailable && (
                    <label className="flex items-start gap-2 mt-5 text-xs text-muted cursor-pointer select-none">
                        <input
                            type="checkbox"
                            checked={branding}
                            onChange={(e) => setBranding(e.target.checked)}
                            className="mt-0.5 accent-[var(--color-accent)] cursor-pointer"
                        />
                        <span>Burn the channel logo into every clip, clear of the captions and the platform UI.</span>
                    </label>
                )}

                {requireRights && (
                    <label className="flex items-start gap-2 mt-5 text-xs text-muted cursor-pointer select-none">
                        <input
                            type="checkbox"
                            checked={acknowledged}
                            onChange={(e) => setAcknowledged(e.target.checked)}
                            className="mt-0.5 accent-[var(--color-accent)] cursor-pointer"
                        />
                        <span>
                            I confirm I own this content or have the rights to process it. I am responsible for any content I submit. See our <a href="/#legal" target="_blank" rel="noopener noreferrer" className="text-ink2 underline underline-offset-2 hover:text-brass transition-colors" onClick={(e) => e.stopPropagation()}>Terms & Privacy</a>.
                        </span>
                    </label>
                )}

                <button
                    type="submit"
                    disabled={isProcessing || !rightsOk || (mode === 'url' && !url) || (mode === 'file' && !file) || (mode === 'local' && localSelected.size === 0)}
                    className="w-full btn-primary mt-4"
                >
                    {isProcessing ? (
                        <>
                            <Loader2 size={16} className="animate-spin" />
                            Processing Video...
                        </>
                    ) : mode === 'local' && localSelected.size > 1 ? (
                        <>
                            Generate Clips · {localSelected.size} files
                        </>
                    ) : (
                        <>
                            Generate Clips
                        </>
                    )}
                </button>
            </form>
        </div>
    );
}
