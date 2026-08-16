// Caption look vocabulary, shared by the per-clip subtitle modal and the
// server's default-style panel. One list, so a preset you pick in Settings is
// the same preset you'd pick on a clip.

export const FONT_OPTIONS = [
    { value: 'Anton', label: 'Anton' },
    { value: 'Verdana', label: 'Verdana' },
    { value: 'Arial', label: 'Arial' },
    { value: 'Impact', label: 'Impact' },
    { value: 'Helvetica', label: 'Helvetica' },
    { value: 'Georgia', label: 'Georgia' },
    { value: 'Courier New', label: 'Courier New' },
];

export const COLOR_PRESETS = [
    { color: '#FFFFFF', label: 'White' },
    { color: '#FFFF00', label: 'Yellow' },
    { color: '#00FFFF', label: 'Cyan' },
    { color: '#00FF00', label: 'Green' },
    { color: '#FF0000', label: 'Red' },
    { color: '#FF69B4', label: 'Pink' },
];

export const HIGHLIGHT_PRESETS = [
    { color: '#FFDD00', label: 'Gold' },
    { color: '#FF4444', label: 'Red' },
    { color: '#00FF88', label: 'Green' },
    { color: '#00BBFF', label: 'Blue' },
    { color: '#FF69B4', label: 'Pink' },
];

// Ready-made caption looks burned server-side as karaoke ASS (word highlight):
// dimmed base text + strong active word, optional glow/pop/box effect.
export const CAPTION_PRESETS = [
    { id: 'tiktok',  label: 'TikTok',     style: 'karaoke', effect: 'none', highlightColor: '#FE2C55', baseOpacity: 0.75, uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'reels',   label: 'Reels',      style: 'karaoke', effect: 'none', highlightColor: '#E1306C', baseOpacity: 0.7,  uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'shorts',  label: 'Shorts Pop', style: 'karaoke', effect: 'pop',  highlightColor: '#FF0000', baseOpacity: 0.7,  uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'gold',    label: 'Gold Glow',  style: 'karaoke', effect: 'glow', highlightColor: '#FFD700', baseOpacity: 0.6,  uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'neon',    label: 'Neon',       style: 'karaoke', effect: 'glow', highlightColor: '#00FF88', baseOpacity: 0.55, uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'cyber',   label: 'Cyber',      style: 'karaoke', effect: 'glow', highlightColor: '#00FFFF', baseOpacity: 0.5,  uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'karaoke', label: 'Karaoke',    style: 'karaoke', effect: 'none', highlightColor: '#FF6B6B', baseOpacity: 0.6,  uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'minimal', label: 'Minimal',    style: 'karaoke', effect: 'none', highlightColor: '#FFFFFF', baseOpacity: 0.65, uppercase: false, fontName: 'Verdana', borderWidth: 1 },
    { id: 'beast',   label: 'Beast',      style: 'karaoke', effect: 'pop',  highlightColor: '#FFD700', baseOpacity: 1.0,  uppercase: true,  fontName: 'Impact',  borderWidth: 3 },
    { id: 'boxed',   label: 'Boxed',      style: 'karaoke', effect: 'box',  highlightColor: '#7C3AED', baseOpacity: 0.85, uppercase: false, fontName: 'Verdana', borderWidth: 2 },
    { id: 'classic', label: 'Classic',    style: 'classic', effect: 'none', highlightColor: '#FFD700', baseOpacity: 1.0,  uppercase: false, fontName: 'Verdana', borderWidth: 2 },
];

// The pipeline's built-in look — what a server with no style.json renders.
// Mirrors subtitles.AUTO_CAPTION_STYLE; the panel starts here so "reset" means
// something concrete.
export const DEFAULT_CAPTION_STYLE = {
    font_name: 'Anton',
    font_size: 44,
    font_color: '#FFFFFF',
    highlight_color: '#FFE500',
    border_color: '#000000',
    border_width: 4,
    style: 'karaoke',
    effect: 'pop',
    alignment: 'bottom',
    base_opacity: 1.0,
    uppercase: true,
    max_chars: 16,
    max_duration: 1.4,
};

// A caption preset from the list above, expressed in the server's style.json
// vocabulary. The two vocabularies differ (camelCase in the modal, snake_case
// on the wire), and this is the one place that knows it.
export function presetToCaptionStyle(preset) {
    return {
        ...DEFAULT_CAPTION_STYLE,
        style: preset.style,
        effect: preset.effect,
        highlight_color: preset.highlightColor,
        base_opacity: preset.baseOpacity,
        uppercase: preset.uppercase,
        font_name: preset.fontName,
        border_width: preset.borderWidth,
        font_color: '#FFFFFF',
    };
}

export const HOOK_STYLE_OPTIONS = [
    { value: 'classic', label: 'White card' },
    { value: 'dark', label: 'Dark card' },
    { value: 'yellow', label: 'Yellow card' },
    { value: 'red', label: 'Red card' },
    { value: 'outline', label: 'Outlined text' },
    { value: 'outline_yellow', label: 'Outlined yellow' },
];

export const LAYOUT_OPTIONS = [
    { value: 'auto', label: 'auto', hint: 'let the AI pick per video' },
    { value: 'split', label: 'split', hint: 'two speakers stacked' },
    { value: 'screencast', label: 'screencast', hint: 'slides over the speaker' },
    { value: 'speaker_cut', label: 'speaker cut', hint: 'cut to whoever talks' },
    { value: 'punch_in', label: 'punch in', hint: 'small push on the beats' },
];
