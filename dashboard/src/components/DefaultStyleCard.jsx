import React, { useState, useEffect, useCallback } from 'react';
import { Palette, Loader2, Check, RotateCcw } from 'lucide-react';
import { apiJson } from '../lib/api';
import {
    FONT_OPTIONS,
    COLOR_PRESETS,
    HIGHLIGHT_PRESETS,
    CAPTION_PRESETS,
    DEFAULT_CAPTION_STYLE,
    HOOK_STYLE_OPTIONS,
    LAYOUT_OPTIONS,
    presetToCaptionStyle,
} from '../lib/captionPresets';

// The look every job on this server starts from — the same style.json the
// pipeline reads at submit time. Setting it here is what makes a batch, a cron
// or an agent produce your look without carrying a single style field.
//
// Self-host only. On a shared instance one server-wide default is meaningless
// (whoever saved last would restyle everybody's clips), so the API serves this
// read-only there and the card says so instead of pretending to save.

const swatchClass = (selected) =>
    `w-6 h-6 rounded-full transition-all ${selected
        ? 'ring-2 ring-[color:var(--color-accent)] ring-offset-2 ring-offset-[color:var(--color-paper-2)]'
        : 'ring-1 ring-[color:var(--color-rule-2)] hover:ring-[color:var(--color-accent)]'}`;

const DEFAULT_HOOK = {
    enabled: false,
    style: 'outline',
    position: 'top',
    size: 'M',
    duration_seconds: 3,
};

export default function DefaultStyleCard() {
    const [loaded, setLoaded] = useState(false);
    const [editable, setEditable] = useState(true);
    const [path, setPath] = useState('');
    const [captions, setCaptions] = useState(DEFAULT_CAPTION_STYLE);
    const [hook, setHook] = useState(DEFAULT_HOOK);
    const [layouts, setLayouts] = useState([]);
    const [outputFormat, setOutputFormat] = useState('auto');
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        apiJson('/api/style')
            .then((d) => {
                const style = d.style || {};
                setCaptions({ ...DEFAULT_CAPTION_STYLE, ...(style.captions || {}) });
                setHook({ ...DEFAULT_HOOK, ...(style.hook || {}) });
                setLayouts(Array.isArray(style.layouts) ? style.layouts : []);
                setOutputFormat(style.output_format || 'auto');
                setEditable(d.editable !== false);
                setPath(d.path || '');
            })
            .catch(() => { /* no preset yet: the built-in defaults above stand */ })
            .finally(() => setLoaded(true));
    }, []);

    // Any edit invalidates the "saved" tick — otherwise it reads as though the
    // change on screen is the one on disk.
    const edit = useCallback((fn) => (...args) => { setSaved(false); fn(...args); }, []);

    const toggleLayout = edit((value) => {
        setLayouts((prev) => (prev.includes(value)
            ? prev.filter((l) => l !== value)
            : [...prev, value]));
    });

    const save = useCallback(async () => {
        setSaving(true);
        try {
            await apiJson('/api/style', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    style: {
                        captions,
                        hook,
                        layouts,
                        output_format: outputFormat,
                    },
                }),
            });
            setSaved(true);
        } catch (e) {
            alert(e?.detail || 'Could not save the default style.');
        }
        setSaving(false);
    }, [captions, hook, layouts, outputFormat]);

    const reset = edit(() => {
        setCaptions(DEFAULT_CAPTION_STYLE);
        setHook(DEFAULT_HOOK);
        setLayouts([]);
        setOutputFormat('auto');
    });

    if (!loaded) {
        return (
            <div className="card p-6 flex justify-center">
                <Loader2 className="animate-spin text-brass" size={18} />
            </div>
        );
    }

    return (
        <div className="card p-6">
            <h3 className="font-display lowercase text-lg text-ink mb-1 flex items-center gap-2">
                <Palette size={16} className="text-brass" /> Default style
            </h3>
            <p className="text-muted text-sm mb-5">
                The look every new job starts from — captions, hook, layouts. Batches, the
                CLI, n8n and MCP agents all inherit it without sending anything, so you set
                it once instead of restyling each clip.
                {path && <> Stored in <code className="readout">{path}</code>.</>}
            </p>

            {!editable && (
                <div className="mb-5 rounded-card border border-rule bg-paper3 p-3 text-sm text-muted">
                    Editing the server default is a self-host setting — on a shared instance
                    it would restyle everybody's clips. Style each clip from the subtitle and
                    hook modals instead.
                </div>
            )}

            <fieldset disabled={!editable} className={!editable ? 'opacity-60' : ''}>
                {/* Caption look */}
                <p className="eyebrow mb-2">Captions</p>
                <div className="flex flex-wrap gap-1.5 mb-4">
                    {CAPTION_PRESETS.map((p) => (
                        <button
                            key={p.id}
                            onClick={edit(() => setCaptions(presetToCaptionStyle(p)))}
                            className="btn-ghost px-2.5 py-1 text-xs"
                        >
                            {p.label}
                        </button>
                    ))}
                </div>

                <div className="grid sm:grid-cols-2 gap-4 mb-4">
                    <label className="block">
                        <span className="text-xs text-muted lowercase">Font</span>
                        <select
                            value={captions.font_name}
                            onChange={edit((e) => setCaptions({ ...captions, font_name: e.target.value }))}
                            className="input-field w-full text-sm mt-1"
                        >
                            {FONT_OPTIONS.map((f) => (
                                <option key={f.value} value={f.value}>{f.label}</option>
                            ))}
                        </select>
                    </label>
                    <label className="block">
                        <span className="text-xs text-muted lowercase">Position</span>
                        <select
                            value={captions.alignment}
                            onChange={edit((e) => setCaptions({ ...captions, alignment: e.target.value }))}
                            className="input-field w-full text-sm mt-1"
                        >
                            <option value="top">top</option>
                            <option value="middle">middle</option>
                            <option value="bottom">bottom</option>
                        </select>
                    </label>
                </div>

                <div className="flex flex-wrap items-center gap-x-6 gap-y-3 mb-5">
                    <div>
                        <span className="text-xs text-muted lowercase block mb-1.5">Text</span>
                        <div className="flex gap-1.5">
                            {COLOR_PRESETS.map((c) => (
                                <button
                                    key={c.color} title={c.label}
                                    onClick={edit(() => setCaptions({ ...captions, font_color: c.color }))}
                                    className={swatchClass(captions.font_color === c.color)}
                                    style={{ backgroundColor: c.color }}
                                />
                            ))}
                        </div>
                    </div>
                    <div>
                        <span className="text-xs text-muted lowercase block mb-1.5">Active word</span>
                        <div className="flex gap-1.5">
                            {HIGHLIGHT_PRESETS.map((c) => (
                                <button
                                    key={c.color} title={c.label}
                                    onClick={edit(() => setCaptions({ ...captions, highlight_color: c.color }))}
                                    className={swatchClass(captions.highlight_color === c.color)}
                                    style={{ backgroundColor: c.color }}
                                />
                            ))}
                        </div>
                    </div>
                    <label className="flex items-center gap-2 text-sm text-ink self-end">
                        <input
                            type="checkbox" checked={!!captions.uppercase}
                            onChange={edit((e) => setCaptions({ ...captions, uppercase: e.target.checked }))}
                        />
                        UPPERCASE
                    </label>
                </div>

                {/* Automatic hook */}
                <p className="eyebrow mb-2">Hook</p>
                <label className="flex items-center gap-2 text-sm text-ink mb-2">
                    <input
                        type="checkbox" checked={!!hook.enabled}
                        onChange={edit((e) => setHook({ ...hook, enabled: e.target.checked }))}
                    />
                    Burn the AI's hook line onto every clip
                </label>
                <p className="text-xs text-muted mb-3 leading-relaxed">
                    The AI already writes one per clip; today it waits for you to open the
                    hook modal. Turning this on puts it on the video automatically.
                </p>
                {hook.enabled && (
                    <div className="grid sm:grid-cols-3 gap-4 mb-5">
                        <label className="block">
                            <span className="text-xs text-muted lowercase">Look</span>
                            <select
                                value={hook.style}
                                onChange={edit((e) => setHook({ ...hook, style: e.target.value }))}
                                className="input-field w-full text-sm mt-1"
                            >
                                {HOOK_STYLE_OPTIONS.map((o) => (
                                    <option key={o.value} value={o.value}>{o.label}</option>
                                ))}
                            </select>
                        </label>
                        <label className="block">
                            <span className="text-xs text-muted lowercase">Position</span>
                            <select
                                value={hook.position}
                                onChange={edit((e) => setHook({ ...hook, position: e.target.value }))}
                                className="input-field w-full text-sm mt-1"
                            >
                                <option value="top">top</option>
                                <option value="center">center</option>
                                <option value="bottom">bottom</option>
                            </select>
                        </label>
                        <label className="block">
                            <span className="text-xs text-muted lowercase">Seconds</span>
                            <input
                                type="number" min="0" step="0.5"
                                value={hook.duration_seconds ?? ''}
                                placeholder="whole clip"
                                onChange={edit((e) => setHook({
                                    ...hook,
                                    duration_seconds: e.target.value === '' ? null : Number(e.target.value),
                                }))}
                                className="input-field w-full text-sm mt-1"
                            />
                        </label>
                    </div>
                )}

                {/* Layouts + format */}
                <p className="eyebrow mb-2">Layouts</p>
                <div className="flex flex-wrap gap-x-5 gap-y-2 mb-4">
                    {LAYOUT_OPTIONS.map((l) => (
                        <label key={l.value} className="flex items-center gap-2 text-sm text-ink" title={l.hint}>
                            <input
                                type="checkbox" checked={layouts.includes(l.value)}
                                onChange={() => toggleLayout(l.value)}
                            />
                            {l.label}
                        </label>
                    ))}
                </div>

                <label className="block mb-5 max-w-[12rem]">
                    <span className="text-xs text-muted lowercase">Output format</span>
                    <select
                        value={outputFormat}
                        onChange={edit((e) => setOutputFormat(e.target.value))}
                        className="input-field w-full text-sm mt-1"
                    >
                        <option value="auto">auto</option>
                        <option value="vertical">vertical</option>
                        <option value="horizontal">horizontal</option>
                        <option value="square">square</option>
                    </select>
                </label>

                <div className="flex gap-2">
                    <button onClick={save} disabled={saving} className="btn-primary py-2 px-4 text-sm">
                        {saving ? <Loader2 size={16} className="animate-spin" />
                            : saved ? <Check size={16} /> : null}
                        {saved && !saving ? 'Saved' : 'Save as default'}
                    </button>
                    <button onClick={reset} className="btn-quiet py-2 px-4 text-sm">
                        <RotateCcw size={15} /> Built-in look
                    </button>
                </div>
            </fieldset>
        </div>
    );
}
