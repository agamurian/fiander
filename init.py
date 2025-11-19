#!/usr/bin/env python3
"""
fiander - full-featured version with text selection
- curses TUI with vim-like bindings + mouse support
- autoscroll, scrolloff, Ctrl-U/D for previews
- syntax-highlighted preview (Gruvbox theme)
- clipboard + catlsr
- TEXT SELECTION: Select multiple lines with mouse or v/V keys, auto-copy to clipboard
"""

import curses, subprocess, os, sys, shutil, traceback, time
from pathlib import Path
import fnmatch, io

# Pygments
try:
    from pygments import lex
    from pygments.lexers import guess_lexer_for_filename, TextLexer
    from pygments.token import Token
    PYGMENTS_AVAILABLE=True
except ImportError:
    PYGMENTS_AVAILABLE=False

# ---------- Config ----------
PREVIEW_MAX_LINES = 400
PREVIEW_MAX_CHARS_PER_LINE = 300
MIN_HEIGHT = 8
MIN_WIDTH = 60
ERROR_LOG = Path("fiander_error.log")

DEFAULT_IGNORE_DIRS = {"__pycache__","node_modules",".git",".hg",".venv","venv","env",".idea",".pytest_cache","dist","build"}
DEFAULT_IGNORE_PATTERNS = {"*.pyc","*.pyo","*.pyd","*.so","*.dll","*.exe","*.class","*.jar","*.lock","*.log","*.db","*.sqlite","*.bak","*.tmp","*.DS_Store"}
DEFAULT_IGNORE_NAMES = {"Thumbs.db"}
SPLITTER = "-"*69
DEFAULT_PREPROMPT = "please analyze this project, add tell how to possibly extend it\n"

# ---------- Helpers ----------
def safe_read_text(path: Path, max_chars: int = PREVIEW_MAX_LINES*PREVIEW_MAX_CHARS_PER_LINE):
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(max_chars)
    except Exception as e:
        return f"[error reading file: {e}]"

def is_text_file(path: Path, read_size: int = 4096):
    try:
        with path.open("rb") as fh:
            chunk = fh.read(read_size)
            return not (chunk and b"\x00" in chunk)
    except Exception: return False

def should_skip(rel: Path, is_dir: bool, gitignore_patterns):
    if is_dir and rel.name in DEFAULT_IGNORE_DIRS: return True
    if rel.name in DEFAULT_IGNORE_NAMES: return True
    for pat in DEFAULT_IGNORE_PATTERNS:
        if fnmatch.fnmatch(rel.name, pat): return True
    for pat in gitignore_patterns:
        neg = pat.startswith("!")
        pattern = pat[1:] if neg else pat
        matched = fnmatch.fnmatch(rel.name, pattern)
        if matched: return not neg
    return False

def walk_for_catlsr(root: Path):
    gitignore_path = root / ".gitignore"
    patterns = []
    if gitignore_path.exists():
        try:
            patterns = [l.strip() for l in gitignore_path.read_text(errors="replace").splitlines() if l.strip() and not l.startswith("#")]
        except Exception: pass
    for top, dirs, files in os.walk(root, topdown=True):
        top_path = Path(top)
        rel_top = top_path.relative_to(root)
        dirs[:] = [d for d in dirs if not should_skip(rel_top/Path(d), True, patterns)]
        for f in files:
            rel = rel_top / f if rel_top.parts else Path(f)
            if should_skip(rel, False, patterns): continue
            if rel.suffix.lower() == ".svg": continue
            fp = root / rel
            if not is_text_file(fp): continue
            yield rel

def read_preprompt(root: Path):
    p = root / "preprompt.txt"
    if p.exists() and p.is_file():
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            return txt if txt.endswith("\n") else txt + "\n"
        except Exception: return DEFAULT_PREPROMPT
    return DEFAULT_PREPROMPT

def generate_catlsr_text(current: Path):
    buf = io.StringIO()
    any_file = False
    for rel in walk_for_catlsr(current):
        any_file = True
        buf.write(f"{SPLITTER}\n{rel}\n{SPLITTER}\n")
        fp = current / rel
        try: buf.write(fp.read_text(errors="replace"))
        except Exception: pass
        buf.write("\n")
    if not any_file: buf.write("[no files found (or all ignored)]\n")
    buf.write(f"{SPLITTER}\npreprompt.txt (special frame)\n{SPLITTER}\n")
    buf.write(read_preprompt(current)+"\n")
    return buf.getvalue()

# ---------- Clipboard ----------
def copy_to_clipboard_verbose(text: str):
    if os.name=="nt":
        try:
            p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.communicate(input=text.encode("utf-8"))
            if p.returncode == 0: return True, "clip.exe"
        except Exception: pass
        try:
            import pyperclip; pyperclip.copy(text); return True, "pyperclip"
        except Exception: return False, "all Windows clipboard methods failed"
    else:
        try:
            import pyperclip; pyperclip.copy(text); return True,"pyperclip"
        except Exception as e: return False,f"pyperclip failed: {e}"

# ---------- TUI state ----------
class TState:
    def __init__(self, cwd: Path):
        self.cwd = cwd.resolve()
        self.history = {}  # Store directory positions: {path: (selected, top_index)}
        try: self.entries = sorted(list(self.cwd.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception: self.entries=[]
        self.selected = 0
        self.top_index = 0
        self.mode = "browser"
        self.input_buffer=""
        self.status="Ready"
        self.last_output=None
        self.show_last_output=False
        self.output_scroll=0
        self.preview_scroll=0
        self.pending_key=None
        self.pending_action=None
        self.clipboard_path=None
        self.clipboard_action=None
        self.preview_selected_line = None
        # Text selection state
        self.selection_mode = False  # Visual mode activated
        self.selection_start = None  # Starting line number
        self.selection_end = None    # Ending line number

    def save_position(self):
        """Save current position for current directory"""
        self.history[str(self.cwd)] = (self.selected, self.top_index)

    def restore_position(self):
        """Restore position for current directory if exists"""
        key = str(self.cwd)
        if key in self.history:
            self.selected, self.top_index = self.history[key]
            # Ensure positions are valid
            if self.selected >= len(self.entries):
                self.selected = max(0, len(self.entries) - 1)
            if self.top_index >= len(self.entries):
                self.top_index = max(0, len(self.entries) - 1)

    def reload(self):
        try: self.entries = sorted(list(self.cwd.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception: self.entries=[]
        self.restore_position()
        if self.selected>=len(self.entries): self.selected=max(0,len(self.entries)-1)
        self.preview_scroll = 0
        self.preview_selected_line = None
        self.clear_selection()
    
    def clear_selection(self):
        """Clear text selection"""
        self.selection_mode = False
        self.selection_start = None
        self.selection_end = None
    
    def get_selected_range(self):
        """Get the ordered range of selected lines"""
        if self.selection_start is None or self.selection_end is None:
            return None, None
        return min(self.selection_start, self.selection_end), max(self.selection_start, self.selection_end)

# ---------- Colors ----------
def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLUE, -1)      # Regular folders
    curses.init_pair(2, curses.COLOR_MAGENTA, -1)   # Functions
    curses.init_pair(3, curses.COLOR_CYAN, -1)      # Classes
    curses.init_pair(4, curses.COLOR_GREEN, -1)     # Symlink folders
    curses.init_pair(5, curses.COLOR_YELLOW, -1)    # Comments/Binaries/Line numbers
    curses.init_pair(6, curses.COLOR_RED, -1)       # Numbers
    curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Selected folder background
    curses.init_pair(8, curses.COLOR_WHITE, -1)     # Simple files / Prompt line
    curses.init_pair(9, curses.COLOR_BLUE, curses.COLOR_WHITE)   # Selection with blue text on white background
    curses.init_pair(10, curses.COLOR_BLUE, -1)     # Folders in preview
    curses.init_pair(11, curses.COLOR_BLACK, curses.COLOR_CYAN)  # Line selection (single line)
    curses.init_pair(12, curses.COLOR_YELLOW, -1)   # Status line
    curses.init_pair(13, curses.COLOR_BLACK, curses.COLOR_GREEN)  # Text selection (multiple lines)

    color_map = {
        Token.Keyword: curses.color_pair(1),
        Token.Keyword.Constant: curses.color_pair(1),
        Token.Keyword.Declaration: curses.color_pair(1),
        Token.Keyword.Namespace: curses.color_pair(1),
        Token.Keyword.Pseudo: curses.color_pair(1),
        Token.Keyword.Reserved: curses.color_pair(1),
        Token.Keyword.Type: curses.color_pair(1),

        Token.Name.Function: curses.color_pair(2),
        Token.Name.Builtin: curses.color_pair(2),
        Token.Name.Class: curses.color_pair(3),
        Token.Name: curses.color_pair(8),

        Token.String: curses.color_pair(4),
        Token.Literal.String: curses.color_pair(4),
        Token.Comment: curses.color_pair(5),
        Token.Operator: curses.color_pair(5),
        Token.Punctuation: curses.color_pair(2),
        Token.Number: curses.color_pair(6),
        
        Token.Name.Decorator: curses.color_pair(2),
        Token.Name.Exception: curses.color_pair(6),
        Token.Name.Variable: curses.color_pair(8),
        Token.Generic: curses.color_pair(5),
    }
    return color_map

# ---------- Drawing ----------
def clipped_addnstr(win,y,x,text,max_width,color=curses.A_NORMAL):
    if y<0 or text is None: return
    try: win.addnstr(y,x,str(text),max_width,color)
    except Exception: pass

def draw_browser(win,st,left_w,height,color_sel):
    for idx in range(height):
        entry_idx = st.top_index + idx
        if entry_idx<len(st.entries):
            entry=st.entries[entry_idx]
            name=entry.name
            if entry.is_dir():
                name += "/"
                # Check if it's a symlink
                if entry.is_symlink():
                    color = curses.color_pair(4) if entry_idx != st.selected else color_sel
                else:
                    color = curses.color_pair(1) if entry_idx != st.selected else color_sel
            elif not is_text_file(entry):
                color = curses.color_pair(5) if entry_idx != st.selected else color_sel
            else:
                color = curses.color_pair(8) if entry_idx != st.selected else color_sel
            clipped_addnstr(win,idx,0,name,left_w-1,color)

def render_with_pygments_curses(win, y, x, path: Path, content: str, color_map, max_lines, max_cols, scroll=0, selected_line=None, selection_start=None, selection_end=None):
    lines = content.splitlines()
    line_num_width = len(str(len(lines))) + 2  # Space for line numbers
    
    if not PYGMENTS_AVAILABLE:
        for i, line in enumerate(lines[scroll:scroll+max_lines]):
            line_num = scroll + i + 1
            is_selected = (selection_start is not None and selection_end is not None and 
                          selection_start <= line_num <= selection_end)
            is_single_cursor = (i == selected_line and not is_selected)
            
            if is_selected:
                line_color = curses.color_pair(13) | curses.A_BOLD  # Green background for selection
            elif is_single_cursor:
                line_color = curses.color_pair(11) | curses.A_BOLD  # Cyan background for cursor
            else:
                line_color = curses.A_NORMAL
            
            # Draw line number in dull color
            try: win.addnstr(y+i, x, f"{line_num:>{line_num_width-1}} ", line_num_width, curses.color_pair(5))
            except: pass
            # Draw line content
            try: win.addnstr(y+i, x+line_num_width, line[:max_cols-line_num_width], max_cols-line_num_width, line_color)
            except: pass
        return
    
    try: lexer = guess_lexer_for_filename(str(path), content)
    except: lexer = TextLexer()
    
    for row, line in enumerate(lines[scroll:scroll+max_lines]):
        line_num = scroll + row + 1
        cx = x + line_num_width
        
        is_selected = (selection_start is not None and selection_end is not None and 
                      selection_start <= line_num <= selection_end)
        is_single_cursor = (row == selected_line and not is_selected)
        
        # Draw line number in dull color
        try: win.addnstr(y+row, x, f"{line_num:>{line_num_width-1}} ", line_num_width, curses.color_pair(5))
        except: pass
        
        if is_selected:
            # Highlight entire selected line with green background and black text
            try: win.addnstr(y+row, cx, line[:max_cols-line_num_width], max_cols-line_num_width, curses.color_pair(13) | curses.A_BOLD)
            except: pass
        elif is_single_cursor:
            # Highlight entire cursor line with cyan background
            try: win.addnstr(y+row, cx, line[:max_cols-line_num_width], max_cols-line_num_width, curses.color_pair(11) | curses.A_BOLD)
            except: pass
        else:
            # Normal syntax highlighting for non-selected lines
            for ttype, value in lex(line, lexer):
                color = curses.A_NORMAL
                parent = ttype
                while parent != Token and parent not in color_map:
                    parent = parent.parent
                if parent in color_map:
                    color = color_map[parent]
                for ch in value:
                    if cx - x - line_num_width >= max_cols - line_num_width: break
                    try: win.addch(y+row, cx, ch, color)
                    except curses.error: pass
                    cx += 1

def draw_preview(win,st,left_w,width,height,color_status,color_map):
    start_x=left_w
    preview_w=max(10,width-left_w)
    for y in range(height): 
        try: win.addch(y,left_w-1,'|')
        except: pass
    if st.pending_action: 
        typ,target=st.pending_action
        clipped_addnstr(win,0,start_x,f"Confirm {typ} {target.name}? (y/n)",preview_w-1)
        return
    if st.show_last_output and st.last_output:
        lines = st.last_output.splitlines()[st.output_scroll:st.output_scroll+height-1]
        for i,line in enumerate(lines): clipped_addnstr(win,i,start_x,line,preview_w-1)
        clipped_addnstr(win,height-1,start_x,"(press 'o' to hide last output)",preview_w-1)
        return
    if not st.entries:
        clipped_addnstr(win,0,start_x,"<empty>",preview_w-1); return
    sel=st.entries[st.selected]
    if sel.is_dir():
        clipped_addnstr(win,0,start_x,"<directory>",preview_w-1)
        try:
            items = sorted(list(sel.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
            for i, child in enumerate(items[:height-1]):
                try:
                    display_path = str(child.relative_to(sel))
                except ValueError:
                    display_path = str(child)
                
                if child.is_dir():
                    display_path += '/'
                    # Check if it's a symlink
                    if child.is_symlink():
                        color = curses.color_pair(4)  # Green for symlink folders
                    else:
                        color = curses.color_pair(10)  # Blue for regular folders
                elif not is_text_file(child):
                    color = curses.color_pair(5)
                else:
                    color = curses.color_pair(8)
                
                clipped_addnstr(win,i+1,start_x,display_path,preview_w-1,color)
        except Exception as e: clipped_addnstr(win,1,start_x,f"[cannot list: {e}]",preview_w-1)
    else:
        if not is_text_file(sel): 
            clipped_addnstr(win,0,start_x,"[binary/non-text]",preview_w-1,curses.color_pair(5)); return
        txt = safe_read_text(sel)
        selected_in_view = None
        if st.preview_selected_line is not None and not st.selection_mode:
            selected_in_view = st.preview_selected_line - st.preview_scroll
            if selected_in_view < 0 or selected_in_view >= (height-1):
                selected_in_view = None
        
        # Get selection range
        sel_start, sel_end = st.get_selected_range()
        
        render_with_pygments_curses(win, 0, start_x, sel, txt, color_map, height-1, preview_w-1, 
                                    scroll=st.preview_scroll, selected_line=selected_in_view,
                                    selection_start=sel_start, selection_end=sel_end)

def draw_status_and_prompt(win,st,width,height,color_status):
    try:
        display_path = st.cwd.name
        status_text = st.status
        if st.selection_mode:
            sel_start, sel_end = st.get_selected_range()
            if sel_start and sel_end:
                status_text += f" [VISUAL: lines {sel_start}-{sel_end}]"
            else:
                status_text += " [VISUAL]"
        # Status line in yellow
        clipped_addnstr(win,height-2,0,f"{display_path}  —  {status_text}"[:width-1],width-1,curses.color_pair(12))
        prompt="> "+st.input_buffer if st.mode=="prompt" else "> (':' for prompt, q quit, o toggles, v/V select, Esc cancel)"
        # Prompt line in white
        clipped_addnstr(win,height-1,0,prompt[:width-1],width-1,curses.color_pair(8))
        if st.mode=="prompt":
            try: win.move(height-1,min(len(prompt),width-1),0)
            except: pass
    except: pass

# ---------- Editor / File openers ----------
def open_in_editor_safe(stdscr,path:Path):
    try: curses.endwin()
    except: pass
    opened=False
    try:
        # Extended MIME type mapping
        suffix = path.suffix.lower()
        if suffix == '.pdf':
            exe = shutil.which("firefox")
            if exe: subprocess.run([exe,str(path)]); opened=True
        elif suffix in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tga', '.tiff'):
            exe = shutil.which("gimp")
            if exe: subprocess.run([exe,str(path)]); opened=True
        elif suffix in ('.blend'):
            exe = shutil.which("blender")
            if exe: subprocess.run([exe,str(path)]); opened=True
        elif suffix in ('.max', '.3ds', '.obj', '.fbx', '.dae'):
            exe = shutil.which("3dsmax")
            if exe: subprocess.run([exe,str(path)]); opened=True
        elif suffix in ('.psd', '.ai', '.eps'):
            # Adobe formats - try default Windows handler
            if sys.platform.startswith("win"):
                os.startfile(str(path)); opened=True
        elif suffix in ('.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'):
            # Office documents - try default Windows handler
            if sys.platform.startswith("win"):
                os.startfile(str(path)); opened=True
        elif suffix in ('.mp4', '.avi', '.mkv', '.mov', '.wmv'):
            # Video files
            if sys.platform.startswith("win"):
                os.startfile(str(path)); opened=True
        elif suffix in ('.mp3', '.wav', '.flac', '.ogg'):
            # Audio files
            if sys.platform.startswith("win"):
                os.startfile(str(path)); opened=True
        else:
            # Default text editor behavior
            exe = shutil.which("nvim")
            if exe: subprocess.run([exe,str(path)]); opened=True
            elif sys.platform.startswith("win"):
                notepad=shutil.which("notepad")
                if notepad: subprocess.run([notepad,str(path)]); opened=True
                else: os.startfile(str(path)); opened=True
            else:
                editor=os.environ.get("EDITOR")
                if editor: subprocess.run([editor,str(path)]); opened=True
                else: opener=shutil.which("xdg-open"); subprocess.run([opener,str(path)]); opened=True
    except Exception: opened=False
    finally:
        try: curses.doupdate(); stdscr.refresh()
        except: pass
    return opened

def open_powershell_at(cwd: Path):
    if not sys.platform.startswith("win"): return False,"PowerShell only on Windows"
    try: subprocess.Popen(["cmd","/c","start","powershell","-NoExit","-Command",f"Set-Location -LiteralPath '{str(cwd)}'"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); return True,"powershell started"
    except Exception as e: return False,f"failed: {e}"

# ---------- File ops ----------
def safe_delete_path(path:Path):
    try:
        if path.is_dir():
            try: path.rmdir()
            except OSError: shutil.rmtree(path)
        else: path.unlink()
        return True,None
    except Exception as e: return False,str(e)

def unique_dest(dest:Path):
    if not dest.exists(): return dest
    base,suffix,parent = dest.stem,dest.suffix,dest.parent
    i=1
    while True:
        candidate=parent/f"{base}_copy{i}{suffix}"
        if not candidate.exists(): return candidate
        i+=1

def perform_paste_action(st:TState):
    if not st.clipboard_path or not st.clipboard_action: return False,"nothing to paste"
    src=Path(st.clipboard_path)
    if not src.exists(): return False,"source missing"
    dst=st.cwd/src.name
    if st.clipboard_action=="copy":
        dst=unique_dest(dst)
        try: shutil.copy2(src,dst); return True,f"copied to {dst.name}"
        except Exception as e: return False,f"copy failed: {e}"
    elif st.clipboard_action=="move":
        dst=unique_dest(dst)
        try: shutil.move(str(src),str(dst)); st.clipboard_path=None; st.clipboard_action=None; return True,f"moved to {dst.name}"
        except Exception as e: return False,f"move failed: {e}"
    return False,"unknown clipboard action"

def copy_selected_text_to_clipboard(st: TState):
    """Copy selected text lines to clipboard"""
    if not st.entries or not st.entries[st.selected].is_file():
        return False, "no file selected"
    
    sel_start, sel_end = st.get_selected_range()
    if sel_start is None or sel_end is None:
        return False, "no text selected"
    
    sel_file = st.entries[st.selected]
    if not is_text_file(sel_file):
        return False, "not a text file"
    
    txt = safe_read_text(sel_file)
    lines = txt.splitlines()
    
    # Extract selected lines (1-indexed to 0-indexed)
    selected_lines = lines[sel_start-1:sel_end]
    selected_text = "\n".join(selected_lines)
    
    ok, info = copy_to_clipboard_verbose(selected_text)
    return ok, info

# ---------- Key handling ----------
def handle_browser_key(st:TState,key,stdscr,color_map):
    if key in (ord('h'),): key=curses.KEY_LEFT
    elif key in (ord('j'),): key=curses.KEY_DOWN
    elif key in (ord('k'),): key=curses.KEY_UP
    elif key in (ord('l'),): key=curses.KEY_RIGHT

    if st.pending_action:
        if key in (ord('y'),ord('Y')):
            typ,target=st.pending_action; st.pending_action=None
            if typ=="delete": ok,msg=safe_delete_path(target); st.status="deleted" if ok else f"delete failed: {msg}"; st.reload()
            return None
        elif key in (ord('n'),ord('N'),27): st.pending_action=None; st.status="cancelled"; return None
        else: return None

    if st.pending_key:
        pk=st.pending_key; st.pending_key=None
        if pk=='d' and key==ord('d'):
            if not st.entries: st.status="nothing selected"; return None
            st.pending_action=("delete",st.entries[st.selected]); st.status=f"Confirm delete {st.entries[st.selected].name}? (y/n)"; return None
        if pk=='y' and key==ord('y'):
            if not st.entries: st.status="nothing selected"; return None
            st.clipboard_path=str(st.entries[st.selected]); st.clipboard_action="copy"; st.status=f"yanked {st.entries[st.selected].name}"; return None
        if pk=='m' and key==ord('m'):
            if not st.entries: st.status="nothing selected"; return None
            st.clipboard_path=str(st.entries[st.selected]); st.clipboard_action="move"; st.status=f"marked {st.entries[st.selected].name} for move"; return None

    # Handle Escape key - cancel selection or pending keys
    if key == 27:
        if st.selection_mode:
            st.clear_selection()
            st.status = "Selection cancelled"
            return None
        elif st.pending_key:
            st.pending_key = None
            st.status = "Cancelled"
            return None

    # Handle visual mode toggle
    if key in (ord('v'), ord('V')):
        if not st.entries or not st.entries[st.selected].is_file():
            st.status = "Visual mode only for text files"
            return None
        if not is_text_file(st.entries[st.selected]):
            st.status = "Visual mode only for text files"
            return None
        
        if not st.selection_mode:
            # Start selection mode
            st.selection_mode = True
            current_line = st.preview_scroll + (st.preview_selected_line - st.preview_scroll if st.preview_selected_line is not None else 0) + 1
            st.selection_start = current_line
            st.selection_end = current_line
            st.status = "VISUAL MODE - move cursor, press v/V again or Esc when done"
        else:
            # End selection mode and copy to clipboard
            ok, info = copy_selected_text_to_clipboard(st)
            st.clear_selection()
            st.status = f"Copied to clipboard ({info})" if ok else f"Copy failed: {info}"
        return None

    height,_ = stdscr.getmaxyx()
    
    # Handle cursor movement in selection mode
    if st.selection_mode and key in (curses.KEY_UP, curses.KEY_DOWN):
        if st.entries and st.entries[st.selected].is_file() and is_text_file(st.entries[st.selected]):
            txt = safe_read_text(st.entries[st.selected])
            total_lines = len(txt.splitlines())
            
            if key == curses.KEY_UP:
                if st.selection_end > 1:
                    st.selection_end -= 1
                    # Auto-scroll if needed
                    if st.selection_end <= st.preview_scroll:
                        st.preview_scroll = max(0, st.selection_end - 1)
            elif key == curses.KEY_DOWN:
                if st.selection_end < total_lines:
                    st.selection_end += 1
                    # Auto-scroll if needed
                    if st.selection_end > st.preview_scroll + (height - 3):
                        st.preview_scroll = st.selection_end - (height - 3)
            return None
    
    if key==curses.KEY_UP:
        st.selected=max(0,st.selected-1)
        if st.selected < st.top_index + 5: st.top_index = max(0, st.selected - 5)
        st.preview_scroll = 0
        st.preview_selected_line = None
        st.clear_selection()
    elif key==curses.KEY_DOWN:
        st.selected=min(len(st.entries)-1,st.selected+1)
        if st.selected >= st.top_index + (height-2)-5: st.top_index = st.selected - (height-2)+5
        st.preview_scroll = 0
        st.preview_selected_line = None
        st.clear_selection()
    elif key==curses.KEY_LEFT:
        st.save_position()  # Save current position before leaving
        parent=st.cwd.parent
        if parent!=st.cwd:
            try: st.cwd=parent.resolve(); st.reload(); st.status=f"cd -> {st.cwd}"; st.show_last_output=False
            except: st.status="Cannot go parent"
    elif key in (curses.KEY_RIGHT,ord("\n")):
        if not st.entries: return None
        sel=st.entries[st.selected]
        if sel.is_dir():
            st.save_position()  # Save current position before entering
            try: st.cwd=sel.resolve(); st.reload(); st.status=f"cd -> {st.cwd}"; st.show_last_output=False
            except: st.status="Cannot enter directory"
        else:
            st.status=f"Opening {sel.name}..."; opened=open_in_editor_safe(stdscr,sel); st.status="Ready" if opened else "No editor found"; st.show_last_output=False
    elif key in (ord(':'),ord('p')): st.mode="prompt"; st.input_buffer=""
    elif key==ord('o'): st.show_last_output=not st.show_last_output
    elif key==curses.KEY_NPAGE:
        st.top_index = min(len(st.entries)-1, st.top_index + (height-2)//2)
    elif key==curses.KEY_PPAGE:
        st.top_index = max(0, st.top_index - (height-2)//2)
    elif key==4:  # Ctrl-D
        sel = st.entries[st.selected] if st.entries else None
        if st.show_last_output and st.last_output:
            st.output_scroll += (height-2)//2
            max_scroll = max(0, len(st.last_output.splitlines()) - (height-2))
            st.output_scroll = min(st.output_scroll, max_scroll)
        elif sel and sel.is_file() and is_text_file(sel):
            st.preview_scroll += (height-2)//2
            txt_lines = safe_read_text(sel).splitlines()
            max_scroll = max(0, len(txt_lines) - (height-2))
            st.preview_scroll = min(st.preview_scroll, max_scroll)
    elif key==21:  # Ctrl-U
        sel = st.entries[st.selected] if st.entries else None
        if st.show_last_output and st.last_output:
            st.output_scroll -= (height-2)//2
            st.output_scroll = max(0, st.output_scroll)
        elif sel and sel.is_file() and is_text_file(sel):
            st.preview_scroll -= (height-2)//2
            st.preview_scroll = max(0, st.preview_scroll)
    elif key==ord('d'): st.pending_key='d'
    elif key==ord('y'): st.pending_key='y'
    elif key==ord('m'): st.pending_key='m'
    elif key==ord('P'): ok,msg=perform_paste_action(st); st.status=msg if ok else f"paste failed: {msg}"; st.reload()
    elif key in (ord('S'),ord('w')): ok,msg=open_powershell_at(st.cwd); st.status=msg if ok else f"powershell failed: {msg}"
    elif key==ord('q'): return "quit"
    elif key == 9:  # Tab key - ignore to prevent crash
        return None
    return None

def handle_prompt_key(st:TState,key):
    if key in (curses.KEY_ENTER,ord("\n")):
        cmd=st.input_buffer.strip()
        if cmd=="catlsr":
            txt=generate_catlsr_text(st.cwd)
            ok,info=copy_to_clipboard_verbose(txt)
            st.last_output=txt; st.show_last_output=True
            st.status=f"[copied to clipboard — method: {info}]" if ok else f"[warning] failed: {info}"
        elif cmd.startswith("cd "):
            st.save_position()  # Save current position before changing
            arg=cmd[3:].strip() or os.path.expanduser("~")
            newdir=(st.cwd/arg).resolve() if not Path(arg).is_absolute() else Path(arg).resolve()
            if newdir.is_dir(): st.cwd=newdir; st.reload(); st.status=f"cd -> {st.cwd}"; st.show_last_output=False
            else: st.status=f"Not a dir: {newdir}"
        elif cmd=="ls": st.reload(); st.status="ls"
        elif cmd in ("exit","quit"): return "quit"
        elif cmd=="help": st.status="Commands: cd <path>, ls, catlsr, quit"
        else: st.status=f"Unknown: {cmd}"
        st.mode="browser"; st.input_buffer=""
    elif key in (curses.KEY_BACKSPACE,127): st.input_buffer=st.input_buffer[:-1]
    elif 32<=key<127: st.input_buffer+=chr(key)
    elif key==27: st.mode="browser"
    elif key == 9:  # Tab key - ignore to prevent crash
        return None
    return None

# ---------- Main loop ----------
def run(stdscr):
    try: curses.curs_set(0)
    except: pass
    stdscr.keypad(True)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    curses.use_default_colors()
    color_map=None
    if curses.has_colors():
        color_map = init_colors()
    st=TState(Path.cwd())
    while True:
        height,width=stdscr.getmaxyx(); stdscr.erase()
        if height<MIN_HEIGHT or width<MIN_WIDTH:
            clipped_addnstr(stdscr,0,0,f"Resize terminal min {MIN_WIDTH}x{MIN_HEIGHT}",width-1); stdscr.refresh(); c=stdscr.getch()
            if c==ord("q"): break
            continue
        left_w=max(24,width//3); left_h=height-2
        # Selected folder gets blue text on white background
        selection_color = curses.color_pair(9) if curses.has_colors() else curses.A_REVERSE
        draw_browser(stdscr,st,left_w,left_h,selection_color)
        draw_preview(stdscr,st,left_w,width,left_h,None,color_map)
        draw_status_and_prompt(stdscr,st,width,height,None)
        try: curses.curs_set(1 if st.mode=="prompt" else 0)
        except: pass
        stdscr.refresh()
        try: key=stdscr.getch()
        except KeyboardInterrupt: break
        except: continue
        if key==curses.KEY_MOUSE:
            try:
                _,mx,my,_,bstate=curses.getmouse()
                if 0<=mx<left_w and 0<=my<left_h:
                    new_sel = st.top_index + my
                    if 0<=new_sel<len(st.entries): 
                        st.selected=new_sel
                        st.preview_scroll=0
                        st.preview_selected_line=None
                        st.clear_selection()
                elif mx>=left_w and 0<=my<left_h:
                    if not st.pending_action:
                        if st.show_last_output and st.last_output:
                            if bstate & curses.BUTTON4_PRESSED:
                                st.output_scroll = max(0, st.output_scroll - 3)
                            elif bstate & curses.BUTTON5_PRESSED:
                                total_lines = len(st.last_output.splitlines())
                                max_scroll = max(0, total_lines - (left_h-1))
                                st.output_scroll = min(max_scroll, st.output_scroll + 3)
                        elif st.entries and st.entries[st.selected].is_file() and is_text_file(st.entries[st.selected]):
                            if bstate & curses.BUTTON1_PRESSED:
                                clicked_line = st.preview_scroll + my + 1
                                if not st.selection_mode:
                                    st.preview_selected_line = clicked_line
                                else:
                                    # In selection mode, update the end position
                                    st.selection_end = clicked_line
                            elif bstate & curses.BUTTON4_PRESSED:
                                st.preview_scroll = max(0, st.preview_scroll - 3)
                            elif bstate & curses.BUTTON5_PRESSED:
                                txt = safe_read_text(st.entries[st.selected])
                                total_lines = len(txt.splitlines())
                                max_scroll = max(0, total_lines - (left_h-1))
                                st.preview_scroll = min(max_scroll, st.preview_scroll + 3)
            except: pass
        if st.mode=="browser": res=handle_browser_key(st,key,stdscr,color_map)
        elif st.mode=="prompt": res=handle_prompt_key(st,key)
        else: res=None
        if res=="quit": break

# ---------- Error logging ----------
def write_error_log(exc:BaseException):
    try:
        tb="".join(traceback.format_exception(type(exc),exc,exc.__traceback__))
        ERROR_LOG.write_text(tb,encoding="utf-8",errors="replace")
    except: pass

def main_safe():
    try:
        if sys.platform.startswith("win"):
            try: import curses
            except: print("Windows: pip install windows-curses"); time.sleep(2.0)
        curses.wrapper(run)
    except Exception as exc:
        write_error_log(exc)
        excerpt="".join(traceback.format_exception_only(type(exc),exc)).strip()
        try: print(f"fiander crashed.\nLog: {ERROR_LOG.resolve()}\nImmediate: {excerpt}",file=sys.stderr)
        except: pass
        try: time.sleep(3.0)
        except: pass

def main(): main_safe()
if __name__=="__main__": main()
