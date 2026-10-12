"""GammaKeeper identity artwork as SVG text: the "keeper" icon, its small-size glyph and the splash page.

The app renders these at run time with Qt's SVG renderer; tools/make_identity.py writes
the committed masters, the multi-size .ico and the README images from the same functions.
Approved design: direction C "keeper" (plan D43), with the plain glyph for 16-24 px.
"""
import math

import numpy as np

NAVY_HI, NAVY_LO = '#1b2d42', '#0c151e'
TEAL, TEAL_HI, GOLD, TEXT, MUTED = '#43d8bf', '#8ff3e1', '#f5bc66', '#e6edf3', '#93a6b8'
APP_ID = 'Fisicas.GammaKeeper'

# Lowercase gamma as one stroke: hooked left arm, crossing, round descender loop, right arm.
GAMMA = ('M56 84 C72 68 96 72 108 100 L148 172 C166 204 158 228 128 228 '
         'C98 228 90 204 108 172 L198 70')

# Radon progeny lines (keV) and relative heights: Pb-214 242/295/352, Bi-214 609/768/1120/1764.
RADON_PEAKS = [(242, .55), (295, .8), (352, 1.0), (609, .9), (768, .18), (1120, .32), (1764, .22)]


def _tile(prefix):
    return (f'<defs><linearGradient id="{prefix}bg" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{NAVY_HI}"/><stop offset="1" stop-color="{NAVY_LO}"/></linearGradient>'
            f'<linearGradient id="{prefix}fg" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{TEAL_HI}"/><stop offset="1" stop-color="{TEAL}"/></linearGradient></defs>'
            f'<rect x="8" y="8" width="240" height="240" rx="54" fill="url(#{prefix}bg)"/>'
            '<rect x="8.75" y="8.75" width="238.5" height="238.5" rx="53.25" fill="none" stroke="#ffffff" '
            'stroke-opacity=".08" stroke-width="1.5"/>')


def _glyph(prefix, width, transform=''):
    return (f'<path d="{GAMMA}" transform="{transform}" fill="none" stroke="url(#{prefix}fg)" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')


def _svg(body, width=256, height=256):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}">{body}</svg>')


def icon_svg(prefix='k'):
    """Main icon: gamma inside a guarding ring with a gold photon in its gap (32 px and up)."""
    ring = (f'<path d="M196.5 66.4 A92 92 0 1 1 168.6 45.2" fill="none" stroke="{TEAL}" stroke-opacity=".9" '
            'stroke-width="11" stroke-linecap="round"/>'
            f'<circle cx="184.8" cy="54.4" r="9.5" fill="{GOLD}"/>')
    return _svg(_tile(prefix) + ring + _glyph(prefix, 22, 'translate(128 132) scale(.72) translate(-127 -148)'))


def small_icon_svg(prefix='s'):
    """Small-size variant: the glyph alone on the tile, legible at 16-24 px."""
    return _svg(_tile(prefix) + _glyph(prefix, 27))


def spectrum_path(x0, x1, y_base, height, n=220, peaks=RADON_PEAKS):
    """Decorative radon-progeny spectrum: falling continuum plus Gaussian photopeaks (~8.5% FWHM at 662 keV)."""
    energy = np.linspace(150, 1900, n)
    y = 0.42 * np.exp(-(energy - 150) / 380) + 0.04
    for centre, amp in peaks:
        sigma = 0.085 * 662 * math.sqrt(centre / 662) / 2.355
        y += amp * np.exp(-0.5 * ((energy - centre) / sigma) ** 2)
    y = y / y.max()
    xs = x0 + (x1 - x0) * (energy - energy[0]) / (energy[-1] - energy[0])
    return 'M' + ' L'.join(f'{x:.1f} {v:.1f}' for x, v in zip(xs, y_base - height * y))


def _escape(text):
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def splash_svg(version, steps=(), run_label=None, run_id=None):
    """Splash page, 720 x 405. steps: (state, text, seconds-or-None), state in done/active/todo."""
    trace = spectrum_path(0, 720, 402, 54)
    icon = icon_svg('p').split('>', 1)[1].rsplit('</svg>', 1)[0]
    rows = []
    for i, (state, text, seconds) in enumerate(steps):
        y = 236 + i * 26
        if state == 'done':
            mark = (f'<path d="M0 0 l5 5 l9 -11" transform="translate(300 {y - 4})" fill="none" stroke="{TEAL}" '
                    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>')
        elif state == 'active':
            mark = f'<circle cx="307" cy="{y - 3}" r="6" fill="none" stroke="{GOLD}" stroke-width="2.4" stroke-dasharray="26 12"/>'
        else:
            mark = f'<circle cx="307" cy="{y - 3}" r="5" fill="none" stroke="{MUTED}" stroke-opacity=".6" stroke-width="1.6"/>'
        colour = MUTED if state == 'todo' else TEXT
        rows.append(mark + f'<text x="326" y="{y + 2}" font-family="Segoe UI" font-size="14" fill="{colour}">{_escape(text)}</text>')
        if seconds is not None:
            rows.append(f'<text x="656" y="{y + 2}" text-anchor="end" font-family="Segoe UI" font-size="13" '
                        f'fill="{MUTED}">{seconds:.1f} s</text>')
    run = ''
    if run_label or run_id:
        run = (f'<text x="64" y="300" font-family="Segoe UI" font-size="12" fill="{MUTED}">Reopening run</text>'
               f'<text x="64" y="320" font-family="Segoe UI" font-weight="600" font-size="14" fill="{TEXT}">{_escape(run_label or "")}</text>'
               f'<text x="64" y="338" font-family="Segoe UI" font-size="11" fill="{MUTED}">{_escape(run_id or "")}</text>')
    body = (f'<defs><linearGradient id="sbg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#14222f"/>'
            '<stop offset="1" stop-color="#0b131b"/></linearGradient>'
            f'<linearGradient id="sfade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TEAL}" stop-opacity=".22"/>'
            f'<stop offset="1" stop-color="{TEAL}" stop-opacity="0"/></linearGradient></defs>'
            '<rect width="720" height="405" rx="14" fill="url(#sbg)"/>'
            f'<path d="{trace} L720 405 L0 405 Z" fill="url(#sfade)"/>'
            f'<path d="{trace}" fill="none" stroke="{TEAL}" stroke-opacity=".55" stroke-width="1.6"/>'
            f'<g transform="translate(64 70) scale(.72)">{icon}</g>'
            f'<text x="300" y="118" font-family="Segoe UI" font-weight="600" font-size="44" fill="{TEXT}">Gamma<tspan fill="{TEAL}">Keeper</tspan></text>'
            f'<text x="302" y="150" font-family="Segoe UI" font-size="15" fill="{MUTED}">Time-resolved gamma spectra • durable experiment logging</text>'
            f'<text x="302" y="190" font-family="Segoe UI" font-size="13" fill="{MUTED}">Version {_escape(version)} • formerly RadiaLog</text>'
            + ''.join(rows) + run +
            f'<text x="690" y="32" text-anchor="end" font-family="Segoe UI" font-size="10" fill="{MUTED}" fill-opacity=".8">'
            'Independent community software • not affiliated with RadiaCode</text>')
    return _svg(body, 720, 405)
