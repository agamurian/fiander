#!/usr/bin/env python3
# fiander_zen_clipfix3_inverted.py — emoji width fixes + simple inverted selection
# This variant uses curses.A_REVERSE for selection highlighting in the code preview

from __future__ import annotations
import os, sys, locale, time, fnmatch, shutil, subprocess, traceback, io
from pathlib import Path
from dataclasses import dataclass, field

# UTF-8 bootstrap
try: locale.setlocale(locale.LC_ALL, '')
except Exception: pass
if os.name == 'nt':
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
try:
    sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Optional libs
try:
    from wcwidth import wcwidth, wcswidth
    HAVE_WCWIDTH = True
except Exception:
    HAVE_WCWIDTH = False

try:
    import curses
except Exception:
    raise SystemExit("curses not available. On Windows: pip install windows-curses")

try:
    from pygments import lex
    from pygments.lexers import guess_lexer_for_filename, TextLexer
    from pygments.token import Token
    PYGMENTS = True
except Exception:
    PYGMENTS = False

# Constants
MIN_W, MIN_H = 40, 8
PREVIEW_MAX = 400 * 300
IGNORE_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "env", ".idea"}
IGNORE_PATTERNS = {"*.pyc", "*.pyo", "*.so", "*.dll", "*.exe", "*.log", "*.db", "*.DS_Store"}
IGNORE_NAMES = {"Thumbs.db"}
SPLIT = "-" * 69
ERRLOG = Path("fiander_error.log")

EMOJI = dict(
    dir="📁", file="📄",
    **{k: v for k, v in {
        '.py':'🐍','.js':'📜','.html':'🌐','.css':'🎨','.md':'📝',
        '.json':'📋','.yml':'⚙️','.yaml':'⚙️','.xml':'📋','.java':'☕',
        '.go':'🐹','.rs':'🦀','.rb':'💎','.c':'🔧','.cpp':'🔧',
        '.png':'🖼️','.jpg':'🖼️','.pdf':'📕','.zip':'📦','.mp4':'🎥',
        '.mp3':'🎵'
    }.items()}
)
SPECIAL = {'dockerfile':'🐳','readme.md':'📖','.gitignore':'🚫','.env':'🔐','license':'📜'}

# Utilities
def log_exc(e: BaseException):
    try:
        ERRLOG.write_text("".join(traceback.format_exception(type(e), e, e.__traceback__)), encoding='utf-8', errors='replace')
    except Exception:
        pass

def is_emoji(ch: str) -> bool:
    """Check if character is likely an emoji"""
    if not ch: return False
    cp = ord(ch)
    # Common emoji ranges
    return (
        0x1F300 <= cp <= 0x1F9FF or  # Misc Symbols and Pictographs, Emoticons, etc.
        0x2600 <= cp <= 0x26FF or    # Misc symbols
        0x2700 <= cp <= 0x27BF or    # Dingbats
        0xFE00 <= cp <= 0xFE0F or    # Variation selectors
        0x1F000 <= cp <= 0x1F02F or  # Mahjong Tiles
        0x1F0A0 <= cp <= 0x1F0FF     # Playing Cards
    )

def display_width(s: str) -> int:
    """Calculate display width, treating emojis as width 2"""
    if s is None: return 0
    total = 0
    for ch in s:
        if is_emoji(ch):
            total += 2  # Emojis are typically 2 cells wide
            continue
        if HAVE_WCWIDTH:
            try:
                cw = wcwidth(ch)
                if cw < 0:  # Treat negative as width 1 (not 0)
                    total += 1
                else:
                    total += cw
            except Exception:
                total += 1
        else:
            total += 1
    return total

def truncate_to(s: str, maxw: int, ell="") -> str:
    """Truncate string to max display width"""
    if s is None: return ""
    if display_width(s) <= maxw: return s
    if maxw <= 0: return ""
    ell_w = display_width(ell)
    aw = maxw - ell_w
    if aw <= 0: return ell if ell_w <= maxw else ""
    
    out, w = [], 0
    for ch in s:
        if is_emoji(ch):
            cw = 2
        elif HAVE_WCWIDTH:
            try:
                cw = wcwidth(ch)
                cw = 1 if cw < 0 else cw
            except Exception:
                cw = 1
        else:
            cw = 1
        
        if w + cw > aw:
            break
        out.append(ch)
        w += cw
    
    return "".join(out) + ell

def clipped_add(win, y, x, txt, maxw, attr=curses.A_NORMAL):
    if maxw <= 0 or y < 0: return
    try:
        out = truncate_to(str(txt) if txt is not None else "", maxw)
        win.addnstr(y, x, out, maxw, attr)
    except curses.error:
        pass
    except Exception:
        try: win.addnstr(y, x, " " * maxw, maxw)
        except Exception: pass

def is_text_file(p: Path, n=4096):
    try:
        with p.open("rb") as fh:
            chunk = fh.read(n)
            return not (chunk and b'\x00' in chunk)
    except Exception:
        return False

def safe_read(p: Path, maxc=PREVIEW_MAX):
    try:
        return p.read_text(encoding='utf-8', errors='replace')[:maxc]
    except Exception as e:
        return f"[error reading file: {e}]"

def emoji_for(p: Path) -> str:
    try:
        if p.is_dir(): return EMOJI['dir']
        n = p.name.lower()
        if n in SPECIAL: return SPECIAL[n]
        ext = p.suffix.lower()
        return EMOJI.get(ext, EMOJI['file'])
    except Exception:
        return EMOJI['file']

# Clipboard helper that prefers clip.exe on Windows
def write_clipboard(text: str) -> tuple[bool, str]:
    if os.name == 'nt':
        try:
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.communicate(input=text.encode("utf-8"))
            if p.returncode == 0:
                return True, "clip.exe"
        except Exception:
            pass
        try:
            import pyperclip
            pyperclip.copy(text)
            return True, "pyperclip"
        except Exception:
            return False, "clipboard failed"
    else:
        try:
            import pyperclip
            pyperclip.copy(text)
            return True, "pyperclip"
        except Exception:
            pass
        for cmd in ("pbcopy", "wl-copy", "xclip"):
            if shutil.which(cmd):
                try:
                    p = subprocess.Popen([cmd], stdin=subprocess.PIPE)
                    p.communicate(input=text.encode("utf-8"))
                    return (p.returncode == 0, cmd)
                except Exception:
                    pass
        return False, "no-clipboard-backend"

# File walking / search
def load_gitignore(root: Path):
    g = root / ".gitignore"
    if not g.exists(): return []
    try:
        return [l.strip() for l in g.read_text(errors='replace').splitlines() if l.strip() and not l.startswith('#')]
    except Exception:
        return []

def should_skip(rel: Path, is_dir: bool, gitp):
    n = rel.name
    if is_dir and n in IGNORE_DIRS: return True
    if n in IGNORE_NAMES: return True
    for pat in IGNORE_PATTERNS:
        if fnmatch.fnmatch(n, pat): return True
    for pat in gitp:
        neg = pat.startswith('!')
        pattern = pat[1:] if neg else pat
        if fnmatch.fnmatch(n, pattern): return not neg
    return False

def walk_files(root: Path, text_only=True):
    gitp = load_gitignore(root)
    for top, dirs, files in os.walk(root, topdown=True):
        top_p = Path(top)
        rel_top = top_p.relative_to(root)
        dirs[:] = [d for d in dirs if not should_skip(rel_top / d, True, gitp)]
        for f in files:
            rel = rel_top / f if rel_top.parts else Path(f)
            if should_skip(rel, False, gitp): continue
            fp = root / rel
            if text_only and (not is_text_file(fp)): continue
            yield rel

def fuzzy_score(name: str, q: str):
    name, q = name.lower(), q.lower()
    if not q: return 0.0
    qi = 0; first = last = None
    for i,ch in enumerate(name):
        if qi < len(q) and ch == q[qi]:
            if first is None: first = i
            last = i; qi += 1
    if qi != len(q): return 0.0
    span = (last - first + 1) if first is not None else len(name)
    return len(q)/span

def search_files(root: Path, q: str, limit=2000):
    res = [(fuzzy_score(str(r), q), str(r)) for r in walk_files(root, text_only=False)]
    res = [s for s in sorted(res, key=lambda x:(-x[0],x[1])) if s[0] > 0]
    return [r for _,r in res][:limit]

def search_lines(root: Path, q: str, limit=2000):
    ql = q.lower(); out=[]
    for rel in walk_files(root, text_only=True):
        fp = root / rel
        try: txt = fp.read_text(errors='replace')
        except Exception: continue
        for i,line in enumerate(txt.splitlines(), 1):
            if ql in line.lower():
                out.append((str(rel), i, line.strip()))
                if len(out) >= limit: return out
    return out

# State
@dataclass
class State:
    cwd: Path = field(default_factory=lambda: Path.cwd().resolve())
    entries: list[Path] = field(default_factory=list)
    selected: int = 0
    top: int = 0
    mode: str = "browser"
    input_buf: str = ""
    status: str = "Ready"
    last_output: str | None = None
    show_output: bool = False
    out_scroll: int = 0
    preview_scroll: int = 0
    preview_line: int | None = None
    selection_mode: bool = False
    sel_start: int | None = None
    sel_end: int | None = None
    clipboard_path: str | None = None
    clipboard_action: str | None = None
    search_mode: str | None = None
    search_results: list = field(default_factory=list)
    search_sel: int = 0
    force_redraw: bool = True
    dir_history: dict = field(default_factory=dict)  # path -> selected filename

    def reload(self, remember_child: Path | None = None):
        try:
            self.entries = sorted(list(self.cwd.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception:
            self.entries = []
        
        # Try to restore selection based on child dir or history
        if remember_child:
            for i, entry in enumerate(self.entries):
                try:
                    if entry.resolve() == remember_child.resolve():
                        self.selected = i
                        break
                except Exception:
                    pass
        elif str(self.cwd) in self.dir_history:
            remembered = self.dir_history[str(self.cwd)]
            for i, entry in enumerate(self.entries):
                if entry.name == remembered:
                    self.selected = i
                    break
        
        # Ensure selected index is valid
        self.selected = min(self.selected, max(0, len(self.entries)-1))

        # Keep preview state reset
        self.preview_scroll = 0
        self.preview_line = None
        self.sel_start = self.sel_end = None

        # IMPORTANT: make the selected entry visible when opening a directory.
        # Place it near the top (with a small offset) so the user sees where they landed.
        try:
            self.top = max(0, self.selected - 2)
        except Exception:
            self.top = 0

        self.force_redraw = True

    def selected_path(self):
        return self.entries[self.selected] if self.entries and 0 <= self.selected < len(self.entries) else None

    def ensure_visible(self, visible_height: int, scrolloff: int = 5):
        """Adjust self.top so the selected entry is visible within the viewport.
        Behavior requested: don't scroll down until selection is at bottom - scrolloff.
        This makes minimal vertical movement and prefers showing the full list when possible.
        """
        try:
            n = len(self.entries)
            if n == 0:
                self.top = 0
                return
            # clamp visible_height to at least 1
            vh = max(1, visible_height)
            # if all entries fit, show from 0
            if n <= vh:
                self.top = 0
                return
            # desired visible window is [top, top+vh-1]
            # Ensure selected is not above the top (scroll up if needed)
            if self.selected < self.top:
                self.top = max(0, self.selected)
                return
            # If selected is below the window: move it so it's at bottom - scrolloff
            if self.selected > (self.top + vh - 1):
                self.top = max(0, min(self.selected - (vh - 1 - scrolloff), n - vh))
                return
            # If selected is too close to bottom (within scrolloff), nudge down
            bottom_threshold = (self.top + vh - 1) - scrolloff
            if self.selected > bottom_threshold:
                new_top = max(0, min(self.selected - (vh - 1 - scrolloff), n - vh))
                self.top = new_top
                return
            # otherwise leave top unchanged (prefer not to scroll)
        except Exception:
            pass

# Curses drawing
def init_colors():
    curses.start_color(); curses.use_default_colors()
    for i,c in enumerate((curses.COLOR_BLUE, curses.COLOR_MAGENTA, curses.COLOR_CYAN,
                         curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED), start=1):
        try: curses.init_pair(i, c, -1)
        except Exception: pass
    try:
        color_map = {
            Token.Keyword: curses.color_pair(1),
            Token.Name.Function: curses.color_pair(2),
            Token.Name.Class: curses.color_pair(3),
            Token.String: curses.color_pair(4),
            Token.Comment: curses.color_pair(5),
            Token.Number: curses.color_pair(6),
        } if PYGMENTS else {}
    except Exception:
        color_map = {}
    return color_map

def draw_browser(win, st: State, leftw: int, height: int, sel_attr):
    # Clear entire browser area first
    for r in range(height):
        try: win.addnstr(r, 0, " "*(leftw-1), leftw-1)
        except Exception: pass
    
    visible = st.entries[st.top:st.top+height]
    for i,entry in enumerate(visible):
        idx = st.top + i
        emo = emoji_for(entry)
        name = entry.name + ('/' if entry.is_dir() else '')
        disp = f"{emo} {name}"
        attr = sel_attr if idx == st.selected else (curses.color_pair(3) if entry.is_dir() else curses.color_pair(0))
        clipped_add(win, i, 0, disp, leftw-1, attr)

def render_text_preview(win, y, x, path: Path, content: str, cmap, nlines, ncols, scroll=0, sel_line=None, sel_range=None):
    lines = content.splitlines()
    lineno_w = len(str(len(lines))) + 2
    sel_low, sel_high = (None, None) if not sel_range else (min(sel_range), max(sel_range))
    # Non-pygments simple rendering — use A_REVERSE for selection/cursor for simple inverted style
    if not PYGMENTS:
        for r, line in enumerate(lines[scroll:scroll+nlines]):
            ln = scroll + r + 1
            sel = (sel_low is not None and sel_low <= ln <= sel_high)
            single = (ln == sel_line and not sel)
            try: win.addnstr(y+r, x, f"{ln:>{lineno_w-1}} ", lineno_w, curses.color_pair(5))
            except Exception: pass
            # Use A_REVERSE for both visual selection and single line cursor
            attr = curses.A_REVERSE | (curses.A_BOLD if sel else 0) if (sel or single) else curses.A_NORMAL
            clipped_add(win, y+r, x+lineno_w, line, ncols-lineno_w, attr)
        return
    try:
        lexer = guess_lexer_for_filename(str(path), content)
    except Exception:
        lexer = TextLexer()
    for r, line in enumerate(lines[scroll:scroll+nlines]):
        ln = scroll + r + 1
        cx = x + lineno_w
        try: win.addnstr(y+r, x, f"{ln:>{lineno_w-1}} ", lineno_w, curses.color_pair(5))
        except Exception: pass
        if sel_low and sel_low <= ln <= sel_high:
            # visual selection range -> inverted + bold
            clipped_add(win, y+r, cx, line, ncols-lineno_w, curses.A_REVERSE | curses.A_BOLD); continue
        if sel_line == ln:
            # current single line -> inverted
            clipped_add(win, y+r, cx, line, ncols-lineno_w, curses.A_REVERSE); continue
        try:
            for ttype, val in lex(line, lexer):
                parent = ttype
                while parent != Token and parent not in cmap:
                    parent = parent.parent
                color = cmap.get(parent, curses.A_NORMAL)
                for ch in val:
                    if (cx - x - lineno_w) >= (ncols - lineno_w): break
                    try: win.addnstr(y+r, cx, ch, 1, color)
                    except Exception: pass
                    cx += 1
        except Exception:
            clipped_add(win, y+r, cx, line, ncols-lineno_w)

def draw_preview(win, st: State, leftw:int, width:int, height:int, cmap):
    sx = leftw
    w = max(10, width - leftw)
    for r in range(height):
        try: win.addnstr(r, sx, " " * (w), w)
        except Exception: pass
    for y in range(height):
        try: win.addch(y, leftw-1, "|")
        except Exception: pass
    if st.show_output and st.last_output:
        lines = st.last_output.splitlines()[st.out_scroll:st.out_scroll+height-1]
        for i,l in enumerate(lines): clipped_add(win, i, sx, l, w-1)
        clipped_add(win, height-1, sx, "(press 'o' to hide output)", w-1)
        return
    sel = st.selected_path()
    if not sel:
        clipped_add(win, 0, sx, "<empty>", w-1); return
    if sel.is_dir():
        clipped_add(win, 0, sx, "<directory>", w-1)
        try:
            items = sorted(list(sel.iterdir()), key=lambda p:(not p.is_dir(), p.name.lower()))
            for i,child in enumerate(items[:height-1]):
                name = f"{emoji_for(child)} {child.name}{'/' if child.is_dir() else ''}"
                clipped_add(win, i+1, sx, name, w-1, curses.color_pair(10) if child.is_dir() else curses.color_pair(8))
        except Exception as e:
            clipped_add(win, 1, sx, f"[cannot list: {e}]", w-1)
    else:
        if not is_text_file(sel):
            clipped_add(win, 0, sx, "[binary/non-text]", w-1, curses.color_pair(5)); return
        txt = safe_read(sel)
        sel_in_view = st.preview_line if st.preview_line and (st.preview_line-1 >= st.preview_scroll) else None
        sel_range = (st.sel_start, st.sel_end) if st.sel_start and st.sel_end else None
        render_text_preview(win, 0, sx, sel, txt, cmap or {}, height-1, w-1, scroll=st.preview_scroll, sel_line=sel_in_view, sel_range=sel_range)

def draw_status(win, st: State, width:int, height:int):
    try:
        clipped_add(win, height-2, 0, f"{st.cwd.name} -> {st.status}"[:width-1], width-1, curses.color_pair(12))
        if st.mode == "prompt": prompt = "> " + st.input_buf
        elif st.mode == "fuzzy": prompt = f"{st.search_mode}> " + st.input_buf
        else: prompt = "> (':' prompt, q quit, o toggles, v visual, Esc cancel)"
        clipped_add(win, height-1, 0, prompt[:width-1], width-1, curses.color_pair(8))
        if st.mode in ("prompt","fuzzy"):
            try: win.move(height-1, min(len(prompt), width-1))
            except Exception: pass
    except Exception:
        pass

# Actions
def unique_dest(p: Path):
    if not p.exists(): return p
    base, suf, parent = p.stem, p.suffix, p.parent
    i = 1
    while True:
        cand = parent / f"{base}_copy{i}{suf}"
        if not cand.exists(): return cand
        i += 1

def perform_paste(st: State):
    if not st.clipboard_path or not st.clipboard_action: return False, "nothing to paste"
    src = Path(st.clipboard_path)
    if not src.exists(): return False, "source missing"
    dst = st.cwd / src.name
    if st.clipboard_action == "copy":
        dst = unique_dest(dst)
        try:
            if src.is_dir(): shutil.copytree(src, dst)
            else: shutil.copy2(src, dst)
            return True, f"copied to {dst.name}"
        except Exception as e: return False, f"copy failed: {e}"
    if st.clipboard_action == "move":
        dst = unique_dest(dst)
        try:
            shutil.move(str(src), str(dst))
            st.clipboard_path = st.clipboard_action = None
            return True, f"moved to {dst.name}"
        except Exception as e: return False, f"move failed: {e}"
    return False, "unknown action"

def copy_selection_to_clipboard(st: State):
    sp = st.selected_path()
    if not sp or not sp.is_file() or not is_text_file(sp): return False, "no text file selected"
    s,e = st.sel_start, st.sel_end
    if s is None or e is None: return False, "no selection"
    txt = safe_read(sp)
    lines = txt.splitlines()
    selected = "\n".join(lines[s-1:e])
    ok, info = write_clipboard(selected)
    return ok, info

# Keys handling
def handle_keys(st: State, key, stdscr, cmap, hist):
    def enter_dir(target: Path):
        # Remember current selection before changing directory
        current_sel = st.selected_path()
        if current_sel:
            st.dir_history[str(st.cwd)] = current_sel.name
        
        old_cwd = st.cwd
        st.cwd = target.resolve()
        
        # If going to parent, remember which child we came from
        remember_child = old_cwd if target == old_cwd.parent else None
        st.reload(remember_child=remember_child)
        
        st.status = f"cd -> {st.cwd}"
        # Force complete redraw on directory change
        stdscr.clear()
        stdscr.refresh()

    if key in (ord('h'),): key = curses.KEY_LEFT
    if key in (ord('j'),): key = curses.KEY_DOWN
    if key in (ord('k'),): key = curses.KEY_UP
    if key in (ord('l'),): key = curses.KEY_RIGHT

    if key in (ord('v'), ord('V')):
        sp = st.selected_path()
        if not sp or not sp.is_file() or not is_text_file(sp):
            st.status = "Visual only for text files"; return None
        if not st.selection_mode:
            st.selection_mode = True
            cur = st.preview_line or (st.preview_scroll + 1)
            st.sel_start = st.sel_end = cur; st.preview_line = cur
            st.status = "VISUAL: move cursor, press v again or Esc"
            return None
        else:
            ok,info = copy_selection_to_clipboard(st)
            st.selection_mode = False; st.status = f"Copied ({info})" if ok else f"Copy failed: {info}"; return None

    if key == 27:
        if st.selection_mode:
            st.selection_mode = False; st.sel_start = st.sel_end = None; st.status = "Selection cancelled"; return None
        if st.mode in ("prompt","fuzzy"):
            st.mode = "browser"; st.input_buf = ""; return None
        if st.search_mode:
            st.search_mode = None; st.search_results = []; st.search_sel = 0; st.status = "search cancelled"; return None

    if st.search_mode:
        if key == curses.KEY_UP:
            st.search_sel = max(0, st.search_sel - 1); st.out_scroll = max(0, st.search_sel); return None
        if key == curses.KEY_DOWN:
            st.search_sel = min(len(st.search_results)-1, st.search_sel + 1); return None
        if key in (ord("\n"), curses.KEY_RIGHT):
            if st.search_mode == "ff":
                if not st.search_results: return None
                target = st.cwd / Path(st.search_results[st.search_sel])
                if target.exists() and target.is_file():
                    st.cwd = st.cwd.resolve(); st.reload()
                    for i,p in enumerate(st.entries):
                        try:
                            if p.resolve() == target.resolve(): st.selected = i; break
                        except Exception: pass
                    st.status = f"Opened {target.name}"; open_in_editor_safe(stdscr, target); st.search_mode=None; st.search_results=[]; return None
            if st.search_mode == "fl":
                rec = st.search_results[st.search_sel]; target = st.cwd / Path(rec[0]); ln = rec[1]
                if target.exists():
                    st.cwd = st.cwd.resolve(); st.reload()
                    for i,p in enumerate(st.entries):
                        try:
                            if p.resolve() == target.resolve(): st.selected = i; break
                        except Exception: pass
                    st.preview_line = ln; st.preview_scroll = max(0, ln-1); st.search_mode=None; st.search_results=[]; st.status = f"Jumped to {rec[0]}:{ln}"; return None
        return None

    sel = st.selected_path()
    h = stdscr.getmaxyx()[0] if stdscr else 25

    if key == curses.KEY_UP:
        st.selected = max(0, st.selected - 1)
        if st.selected < st.top + 5: st.top = max(0, st.selected - 5)
        st.preview_scroll = 0; st.preview_line = None; st.selection_mode=False; st.force_redraw=True
    elif key == curses.KEY_DOWN:
        st.selected = min(len(st.entries)-1, st.selected + 1)
        if st.selected >= st.top + (h-2) - 5: st.top = st.selected - (h-2) + 5
        st.preview_scroll = 0; st.preview_line = None; st.selection_mode=False; st.force_redraw=True
    elif key == curses.KEY_LEFT:
        parent = st.cwd.parent
        if parent != st.cwd:
            try: enter_dir(parent)
            except Exception: st.status = "Cannot go parent"
    elif key == curses.KEY_RIGHT or key == ord("\n"):
        if not sel: return None
        if sel.is_dir():
            try: enter_dir(sel)
            except Exception: st.status = "Cannot enter"
        else:
            st.status = f"Opening {sel.name}..."; open_in_editor_safe(stdscr, sel); st.status = "Ready"
    elif key == ord(':') or key == ord('p'):
        st.mode = "prompt"; st.input_buf = ""
    elif key == ord('o'):
        st.show_output = not st.show_output
    elif key == curses.KEY_NPAGE:
        st.top = min(max(0, len(st.entries)-1), st.top + (h-2)//2); st.force_redraw=True
    elif key == curses.KEY_PPAGE:
        st.top = max(0, st.top - (h-2)//2); st.force_redraw=True
    elif key == 4:
        if st.show_output and st.last_output:
            st.out_scroll = min(max(0, len(st.last_output.splitlines())-(h-2)), st.out_scroll + (h-2)//2)
        elif sel and sel.is_file() and is_text_file(sel):
            txt_lines = safe_read(sel).splitlines(); st.preview_scroll = min(max(0, len(txt_lines)-(h-2)), st.preview_scroll + (h-2)//2)
    elif key == 21:
        if st.show_output and st.last_output:
            st.out_scroll = max(0, st.out_scroll - (h-2)//2)
        elif sel and sel.is_file() and is_text_file(sel):
            st.preview_scroll = max(0, st.preview_scroll - (h-2)//2)
    elif key == ord('d'):
        st.mode = "maybe_delete"; st.input_buf = ""
    elif key == ord('y'):
        st.clipboard_path = str(sel) if sel else None; st.clipboard_action = "copy"; st.status = f"yanked {sel.name if sel else ''}"
    elif key == ord('m'):
        st.clipboard_path = str(sel) if sel else None; st.clipboard_action = "move"; st.status = f"marked {sel.name if sel else ''}"
    elif key == ord('f'):
        st.mode = "fuzzy"; st.input_buf = ""; st.search_mode = "ff"; st.status = "ff: type to fuzzy-search files"
    elif key == ord('P'):
        ok,msg = perform_paste(st); st.status = msg if ok else f"paste failed: {msg}"; st.reload()
    elif key == ord('q'):
        return "quit"
    elif key == 9:
        return None
    else:
        if getattr(st, 'mode', None) == "maybe_delete" and key == ord('d'):
            if sel:
                ok,msg = safe_delete(sel)
                st.reload(); st.status = "deleted" if ok else f"delete failed: {msg}"
            st.mode = "browser"
        else:
            return None
    return None

def safe_delete(p: Path):
    try:
        if p.is_dir():
            try: p.rmdir()
            except OSError: shutil.rmtree(p)
        else:
            p.unlink()
        return True, None
    except Exception as e:
        return False, str(e)

def open_in_editor_safe(stdscr, path: Path):
    try: curses.endwin()
    except Exception: pass
    opened = False
    try:
        exe = shutil.which("nvim") or shutil.which("vim") or shutil.which("code") or shutil.which("subl") or shutil.which("nano")
        if exe:
            subprocess.run([exe, str(path)])
            opened = True
        elif sys.platform.startswith("win"):
            try: os.startfile(str(path)); opened = True
            except Exception: opened = False
        else:
            editor = os.environ.get("EDITOR")
            if editor: subprocess.run([editor, str(path)]); opened = True
            else:
                opener = shutil.which("xdg-open") or shutil.which("open")
                if opener: subprocess.run([opener, str(path)]); opened = True
    except Exception:
        opened = False
    finally:
        try: curses.doupdate(); stdscr.refresh()
        except Exception: pass
    return opened

def handle_prompt(st: State, key):
    if st.mode == "fuzzy":
        if key in (curses.KEY_ENTER, ord("\n")):
            q = st.input_buf.strip()
            if st.search_mode == "ff":
                st.search_results = search_files(st.cwd, q); st.search_mode = "ff"; st.mode = "browser"; st.status = f"ff results: {len(st.search_results)}"
            elif st.search_mode == "fl":
                st.search_results = search_lines(st.cwd, q); st.search_mode = "fl"; st.mode = "browser"; st.status = f"fl results: {len(st.search_results)}"
            st.input_buf = ""; return None
        if key in (curses.KEY_BACKSPACE, 127):
            st.input_buf = st.input_buf[:-1]; return None
        if 32 <= key < 127:
            st.input_buf += chr(key); return None
        if key == 27:
            st.mode = "browser"; st.input_buf = ""; st.search_mode = None; return None
        return None

    if key in (curses.KEY_ENTER, ord("\n")):
        cmd = st.input_buf.strip(); args = cmd.split()
        if not args: st.mode = "browser"; st.input_buf = ""; return None
        try:
            if args[0] == "catlsr":
                st.last_output = generate_catlsr(st.cwd); st.show_output = True; st.status = "[catlsr]"
                ok, info = write_clipboard(st.last_output)
                if ok: st.status += f" (copied via {info})"
            elif args[0] == "cd":
                arg = " ".join(args[1:]) or os.path.expanduser("~")
                nd = (st.cwd / arg).resolve() if not Path(arg).is_absolute() else Path(arg).resolve()
                if nd.is_dir(): st.cwd = nd; st.reload(); st.status = f"cd -> {st.cwd}"
                else: st.status = f"Not a dir: {nd}"
            elif args[0] == "ls":
                st.reload(); st.status = "ls"
            elif args[0] == "rename" and len(args) == 3:
                s = st.cwd / args[1]; d = st.cwd / args[2]
                if s.exists(): s.rename(d); st.reload(); st.status = f"Renamed {s.name} -> {d.name}"
                else: st.status = "Source does not exist."
            elif args[0] == "mkdir" and len(args) == 2:
                (st.cwd / args[1]).mkdir(exist_ok=False); st.reload(); st.status = f"mkdir {args[1]}"
            elif args[0] == "touch" and len(args) == 2:
                (st.cwd / args[1]).touch(exist_ok=False); st.reload(); st.status = f"touch {args[1]}"
            elif args[0] == "duplicate" and len(args) == 2:
                s = st.cwd / args[1]
                if s.exists():
                    dst = unique_dest(st.cwd / (s.stem + s.suffix))
                    if s.is_dir(): shutil.copytree(s, dst)
                    else: shutil.copy2(s, dst)
                    st.reload(); st.status = f"Duplicated {args[1]}"
                else: st.status = "Source not found"
            elif args[0] == "chmod" and len(args) == 3:
                p = st.cwd / args[2]; p.chmod(int(args[1], 8)); st.status = f"chmod {args[1]} {args[2]}"
            elif args[0] == "cat" and len(args) == 2:
                p = st.cwd / args[1]
                if p.exists(): st.last_output = safe_read(p); st.show_output=True; st.status = f"Showing {args[1]}"
                else: st.status = "File does not exist."
            elif args[0] == "move" and len(args) == 3:
                s = st.cwd / args[1]; d = st.cwd / args[2]
                if s.exists(): shutil.move(str(s), str(d)); st.reload(); st.status = f"Moved {args[1]}"
                else: st.status = "Source does not exist."
            elif args[0] in ("exit", "quit"):
                return "quit"
            elif args[0] == "help":
                st.status = "Commands: cd <path>, ls, catlsr, cat <f>, mkdir, touch, rename, duplicate, chmod, move, quit"
            else:
                st.status = f"Unknown: {cmd}"
        except Exception as e:
            st.status = f"Error: {e}"; log_exc(e)
        st.mode = "browser"; st.input_buf = ""; return None
    if key in (curses.KEY_BACKSPACE, 127): st.input_buf = st.input_buf[:-1]; return None
    if 32 <= key < 127: st.input_buf += chr(key); return None
    if key == 27: st.mode = "browser"; st.input_buf = ""; return None
    return None

def generate_catlsr(root: Path):
    buf = io.StringIO(); any_file=False
    for rel in walk_files(root):
        any_file = True
        buf.write(f"{SPLIT}\n{rel}\n{SPLIT}\n")
        try: buf.write((root/rel).read_text(errors='replace'))
        except Exception: pass
        buf.write("\n")
    if not any_file: buf.write("[no files found]\n")
    buf.write(f"{SPLIT}\npreprompt.txt\n{SPLIT}\n{read_preprompt(root)}\n")
    return buf.getvalue()

def read_preprompt(root: Path):
    p = root / "preprompt.txt"
    if p.exists():
        try:
            txt = p.read_text(errors='replace'); return txt if txt.endswith("\n") else txt + "\n"
        except Exception:
            return "please analyze this project, add tell how to possibly extend it\n"
    return "please analyze this project, add tell how to possibly extend it\n"

def main_curses(stdscr):
    try: curses.curs_set(0)
    except Exception: pass
    stdscr.keypad(True)
    try: curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    except Exception: pass

    cmap = init_colors() if curses.has_colors() else {}
    st = State(); st.reload()
    last_check = time.time()

    while True:
        h,w = stdscr.getmaxyx()
        
        # Force clear and redraw if needed
        if st.force_redraw:
            stdscr.clear()
            st.force_redraw = False
        else:
            stdscr.erase()
            
        if h < MIN_H or w < MIN_W:
            clipped_add(stdscr, 0, 0, f"Resize terminal min {MIN_W}x{MIN_H}", w-1)
            stdscr.refresh()
            c = stdscr.getch()
            if c == ord('q'): break
            continue

        if time.time() - last_check > 0.8:
            try:
                entries_now = sorted(list(st.cwd.iterdir()), key=lambda p:(not p.is_dir(), p.name.lower()))
                if [p.name for p in entries_now] != [p.name for p in st.entries]:
                    st.reload(); st.status = "fs changed"
            except Exception:
                pass
            last_check = time.time()

        leftw = max(20, w//4); left_h = h-2
        # Make sure the selected entry is visible within the left pane before drawing.
        try:
            st.ensure_visible(left_h, scrolloff=5)
        except Exception:
            pass
        draw_browser(stdscr, st, leftw, left_h, curses.A_REVERSE)
        draw_preview(stdscr, st, leftw, w, left_h, cmap)
        draw_status(stdscr, st, w, h)
        try: curses.curs_set(1 if st.mode in ("prompt","fuzzy") else 0)
        except Exception: pass
        stdscr.refresh()

        try: key = stdscr.getch()
        except KeyboardInterrupt: break
        except Exception: continue

        if key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
                if 0 <= mx < leftw and 0 <= my < left_h:
                    new = st.top + my
                    if 0 <= new < len(st.entries): st.selected = new; st.preview_scroll = 0; st.preview_line = None; st.selection_mode=False
                elif mx >= leftw and 0 <= my < left_h:
                    if st.show_output and st.last_output:
                        if bstate & curses.BUTTON4_PRESSED: st.out_scroll = max(0, st.out_scroll - 3)
                        elif bstate & curses.BUTTON5_PRESSED:
                            total = len(st.last_output.splitlines()); st.out_scroll = min(max(0, total-(left_h-1)), st.out_scroll + 3)
                    elif st.entries and st.selected_path().is_file():
                        if bstate & curses.BUTTON1_PRESSED:
                            cl = st.preview_scroll + my + 1; st.preview_line = cl
                            if st.selection_mode: st.sel_end = cl
                        elif bstate & curses.BUTTON4_PRESSED: st.preview_scroll = max(0, st.preview_scroll - 3)
                        elif bstate & curses.BUTTON5_PRESSED:
                            txt = safe_read(st.selected_path()); total = len(txt.splitlines()); st.preview_scroll = min(max(0, total-(left_h-1)), st.preview_scroll + 3)
            except Exception:
                pass
            continue

        if st.mode in ("prompt","fuzzy"):
            res = handle_prompt(st, key)
            if res == "quit": break
            continue

        try:
            res = handle_keys(st, key, stdscr, cmap, {})
            if res == "quit": break
        except Exception as e:
            log_exc(e)
            st.status = f"error: {e}"

def main():
    try:
        if sys.platform.startswith("win"):
            try: import curses as _;
            except Exception:
                print("Windows: please install windows-curses (pip install windows-curses)"); time.sleep(1.0)
        curses.wrapper(main_curses)
    except Exception as e:
        log_exc(e)
        try: print("fiander_zen_clipfix3 crashed. See fiander_error.log", file=sys.stderr)
        except Exception: pass

if __name__ == "__main__":
    main()
