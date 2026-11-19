"""trirv_cc —— 面向 TriRV 的 C 子集编译器（教学型，自举链路关键一环）。

支持：
  - int 类型（全局/局部变量）
  - 算术 + - * / %，比较 < <= > >= == !=，逻辑 && || !，位运算 & | ^ << >>
  - 赋值 =（变量），一元 - + ! ~
  - 语句：if/else、while、for、return、块、表达式语句、break/continue
  - 函数定义与调用（寄存器传参 a0-a7，返回值 a0，栈帧支持递归）
  - // 与 /* */ 注释

代码生成：栈式求值，复用 trirv_asm.assemble 生成 20-trit 机器码。
输出：可直接喂给 VM 的指令槽列表。

调用约定（对齐 RISC-V 精简版）：
  a0-a7 = x10..x17（参数/返回），ra=x1，sp=x2，fp=s0=x8
"""

import re
import trirv_asm as asm
import trirv_isa as isa

# ---------------- 词法分析 ----------------
TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*[\s\S]*?\*/)
  | (?P<str>"(?:\\.|[^"\\])*")
  | (?P<char>'(?:\\.|[^'\\])')
  | (?P<num>0[xX][0-9a-fA-F]+|\d+)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<op>\.\.\.|==|!=|<=|>=|&&|\|\||<<|>>|\+\+|--|->|\.|[-+*/%=<>!&|^~?,;(){}\[\]:])
""", re.X)

KEYWORDS = {"int","char","void","struct","va_list","unsigned","signed",
            "if","else","while","for","return","break","continue"}

class Tok:
    def __init__(self, kind, val): self.kind, self.val = kind, val
    def __repr__(self): return f"Tok({self.kind},{self.val!r})"

def lex(src):
    toks, i = [], 0
    while i < len(src):
        m = TOKEN_RE.match(src, i)
        if not m:
            raise SyntaxError(f"无法识别字符 {src[i]!r} @ {i}")
        i = m.end()
        k = m.lastgroup
        if k in ("ws","lc","bc"): continue
        v = m.group()
        if k == "id" and v in KEYWORDS: toks.append(Tok("kw", v))
        elif k == "id": toks.append(Tok("id", v))
        elif k == "num": toks.append(Tok("num", int(v, 0)))
        elif k == "str":
            # 解析 C 转义：\n \t \r \\ \" \0
            body = v[1:-1]
            out = bytearray(); j = 0
            esc = {"n":10, "t":9, "r":13, "\\":92, '"':34, "0":0}
            while j < len(body):
                if body[j] == "\\" and j + 1 < len(body):
                    out.append(esc.get(body[j+1], ord(body[j+1]))); j += 2
                else:
                    out.append(ord(body[j]) & 0xFF); j += 1
            toks.append(Tok("str", bytes(out)))
        elif k == "char":
            # 字符字面量 'x' / '\n' -> 对应整数值
            body = v[1:-1]
            esc = {"n":10, "t":9, "r":13, "\\":92, "'":39, '"':34, "0":0}
            if len(body) == 1: val = ord(body)
            else: val = esc.get(body[1], ord(body[1]))
            toks.append(Tok("num", val))
        else: toks.append(Tok("op", v))
    toks.append(Tok("eof", None))
    return toks

# ---------------- AST ----------------
class Node:
    def __init__(self, kind, **kw): self.kind = kind; self.__dict__.update(kw)
    def __repr__(self): return f"Node({self.kind})"

# ---------------- 类型布局工具（模块级，Parser / CodeGen 共用）----------------
WORD_TRYTES = 9
def align9(n):
    """向上对齐到 WORD_TRYTES（int/指针宽度）边界。"""
    return ((n + WORD_TRYTES - 1) // WORD_TRYTES) * WORD_TRYTES

def type_size(ty, structs):
    """计算类型占用 tryte 数。structs: {name: {"size":N,"members":[...]}}。"""
    m = re.match(r"^(.+)\[(\d+)\]$", ty)
    if m: return int(m.group(2)) * type_size(m.group(1), structs)
    if ty.endswith("*"): return WORD_TRYTES
    if ty in ("char", "uchar", "schar"): return 1
    if ty.startswith("struct "):
        name = ty[len("struct "):]
        info = structs.get(name)
        if not info: raise SyntaxError(f"未知结构体: {name}")
        return info["size"]
    return WORD_TRYTES                      # int / unsigned / void

def is_unsigned(ty):
    """是否为无符号整型（unsigned / unsigned int 归一化为 'unsigned'）。

    注意 char 也是无符号——RISC-V psABI 规定 plain char 无符号（GCC
    DEFAULT_SIGNED_CHAR=0），TriRV 的 tryte 值域 0..242 与之天然吻合。
    """
    return ty in ("unsigned", "char", "uchar")

def is_struct(ty):
    return ty.startswith("struct ") and not ty.endswith("*")

# ---------------- 递归下降解析 ----------------
class Parser:
    STR_BASE = 100                       # 字符串常量的 byte 内存起始地址
    def __init__(self, toks):
        self.toks = toks; self.i = 0
        self.strings = {}                # addr -> bytes
        self.str_ptr = self.STR_BASE
        self.structs = {}                # name -> {"size":N, "members":[(mname,mtype,offset)]}
    def peek(self): return self.toks[self.i]
    def at(self, v):
        t = self.peek(); return t.val == v and t.kind in ("op","kw")
    def take(self, v=None):
        t = self.peek()
        if v is not None and not self.at(v):
            raise SyntaxError(f"期望 {v!r}，实际 {t!r}")
        self.i += 1; return t
    def accept(self, v):
        if self.at(v): self.i += 1; return True
        return False

    def parse_program(self):
        fns = []
        while self.peek().kind != "eof":
            if self.at("struct") and self._struct_is_def():
                self.parse_struct_def()
            else:
                fns.append(self.parse_func())
        return fns

    def _struct_is_def(self):
        """区分 `struct S {` 定义与 `struct S f(...)` 类型使用。"""
        j = self.i + 1
        return (j + 1 < len(self.toks) and self.toks[j + 1].val == "{")

    def parse_struct_def(self):
        """struct S { T m; ... };  ——  成员起始对齐到 WORD_TRYTES。"""
        self.take("struct")
        name = self.take().val
        self.take("{")
        members = []; cur = 0
        while not self.at("}"):
            mty = self.parse_type()
            mname = self.take().val
            off = align9(cur)
            cur = off + type_size(mty, self.structs)
            members.append((mname, mty, off))
            self.take(";")
        self.take("}")
        self.take(";")
        self.structs[name] = {"size": align9(cur), "members": members}

    def parse_type(self):
        """解析类型 + 0+ 个 *，返回类型字符串（int / char* / struct S / struct S*）。"""
        if self.at("unsigned"):
            self.take("unsigned")
            if self.accept("char"):
                base = "uchar"                          # unsigned char：1 tryte，零扩展
            else:
                self.accept("int")                      # unsigned 与 unsigned int 等价
                base = "unsigned"
        elif self.at("signed"):
            self.take("signed")
            if self.accept("char"):
                base = "schar"                          # signed char：1 tryte，符号扩展
            else:
                self.accept("int")
                base = "int"
        elif self.at("int") or self.at("char") or self.at("void"):
            base = self.take().val
        elif self.at("va_list"):
            self.take("va_list"); base = "va_list"     # 变参游标，WORD_TRYTES
        elif self.at("struct"):
            self.take("struct"); base = "struct " + self.take().val
        else: raise SyntaxError(f"期望类型，实际 {self.peek()!r}")
        while self.at("*"):
            self.take(); base += "*"
        return base

    def parse_func(self):
        ret = self.parse_type()
        name = self.take().val
        self.take("(")
        params = []                       # [(name, ty)]
        variadic = False
        while not self.at(")"):
            if self.accept("..."):        # 变参标记（必须位于形参表末尾）
                variadic = True
                break
            ty = self.parse_type()
            pname = self.take().val
            dims = []
            while self.accept("["):       # 数组形参：首维退化指针，其余维度保留
                dims.append(self.take().val if not self.at("]") else None)
                self.take("]")
            if dims:
                for d in dims[1:]:
                    ty += f"[{d}]"
                ty += "*"
            params.append((pname, ty))
            if not self.accept(","): break
        self.take(")")
        body = self.parse_block()
        return Node("func", name=name, ret=ret, params=params, body=body,
                    variadic=variadic)

    def parse_block(self):
        self.take("{")
        stmts = []
        while not self.at("}") and self.peek().kind != "eof":
            stmts.append(self.parse_stmt())
        self.take("}")
        return Node("block", stmts=stmts)

    def parse_stmt(self):
        if self.at("{"): return self.parse_block()
        if self.at("if"):
            self.take("if"); self.take("(")
            cond = self.parse_expr(); self.take(")")
            then = self.parse_stmt()
            els = self.parse_stmt() if self.accept("else") else None
            return Node("if", cond=cond, then=then, els=els)
        if self.at("while"):
            self.take("while"); self.take("(")
            cond = self.parse_expr(); self.take(")")
            body = self.parse_stmt()
            return Node("while", cond=cond, body=body)
        if self.at("for"):
            self.take("for"); self.take("(")
            init = self.parse_expr() if not self.at(";") else None; self.take(";")
            cond = self.parse_expr() if not self.at(";") else None; self.take(";")
            step = self.parse_expr() if not self.at(")") else None; self.take(")")
            body = self.parse_stmt()
            return Node("for", init=init, cond=cond, step=step, body=body)
        if self.at("return"):
            self.take("return")
            e = self.parse_expr() if not self.at(";") else Node("num", val=0)
            self.take(";")
            return Node("return", expr=e)
        if self.at("break"):
            self.take("break"); self.take(";"); return Node("break")
        if self.at("continue"):
            self.take("continue"); self.take(";"); return Node("continue")
        if self.at("int") or self.at("char") or self.at("struct") or self.at("va_list") or self.at("unsigned") or self.at("signed"):
            ty = self.parse_type()
            decls = []
            while True:
                name = self.take().val
                dty = ty
                while self.accept("["):         # 多维数组：int a[2][3]
                    cnt = self.take().val        # 维度必须是常量
                    self.take("]")
                    dty += f"[{cnt}]"
                init = self.parse_initializer() if self.accept("=") else None
                decls.append((name, dty, init))
                if not self.accept(","): break
            self.take(";")
            return Node("decl", decls=decls)
        e = self.parse_expr(); self.take(";")
        return Node("expr", expr=e)

    def parse_initializer(self):
        """初始化器：{item, item, ...} 列表，或单表达式。列表可嵌套。"""
        if self.accept("{"):
            items = []
            while not self.at("}"):
                items.append(self.parse_initializer())
                if not self.accept(","): break
            self.take("}")
            return Node("initlist", items=items)
        return self.parse_expr()

    # 优先级由低到高
    def parse_expr(self): return self.parse_assign()
    def parse_assign(self):
        left = self.parse_or()
        if self.at("="):
            self.take(); right = self.parse_assign()
            if left.kind not in ("var", "deref", "index", "member"):
                raise SyntaxError("赋值左值必须是变量/*解引用/[]下标/结构成员")
            return Node("assign", lv=left, expr=right)
        return left
    def parse_or(self):
        n = self.parse_and()
        while self.accept("||"): n = Node("bin", op="||", l=n, r=self.parse_and())
        return n
    def parse_and(self):
        n = self.parse_bitor()
        while self.accept("&&"): n = Node("bin", op="&&", l=n, r=self.parse_bitor())
        return n
    def parse_bitor(self):
        n = self.parse_bitxor()
        while self.at("|") and not self.at("||"):
            self.take(); n = Node("bin", op="|", l=n, r=self.parse_bitxor())
        return n
    def parse_bitxor(self):
        n = self.parse_bitand()
        while self.accept("^"): n = Node("bin", op="^", l=n, r=self.parse_bitand())
        return n
    def parse_bitand(self):
        n = self.parse_eq()
        while self.at("&") and not self.at("&&"):
            self.take(); n = Node("bin", op="&", l=n, r=self.parse_eq())
        return n
    def parse_eq(self):
        n = self.parse_rel()
        while self.at("==") or self.at("!="):
            op = self.take().val; n = Node("bin", op=op, l=n, r=self.parse_rel())
        return n
    def parse_rel(self):
        n = self.parse_shift()
        while self.val_in("<", "<=", ">", ">="):
            op = self.take().val; n = Node("bin", op=op, l=n, r=self.parse_shift())
        return n
    def parse_shift(self):
        n = self.parse_add()
        while self.val_in("<<", ">>"):
            op = self.take().val; n = Node("bin", op=op, l=n, r=self.parse_add())
        return n
    def parse_add(self):
        n = self.parse_mul()
        while self.val_in("+", "-"):
            op = self.take().val; n = Node("bin", op=op, l=n, r=self.parse_mul())
        return n
    def parse_mul(self):
        n = self.parse_unary()
        while self.val_in("*", "/", "%"):
            op = self.take().val; n = Node("bin", op=op, l=n, r=self.parse_unary())
        return n
    def parse_unary(self):
        if self.accept("&"): return Node("addr", e=self.parse_unary())
        if self.accept("*"): return Node("deref", e=self.parse_unary())
        if self.val_in("-", "+", "!", "~"):
            op = self.take().val; return Node("un", op=op, e=self.parse_unary())
        if self.accept("++"): return Node("preinc", lv=self.parse_unary(), d=1)
        if self.accept("--"): return Node("preinc", lv=self.parse_unary(), d=-1)
        return self.parse_postfix()
    def parse_postfix(self):
        n = self.parse_primary()
        while True:
            if self.at("++") or self.at("--"):
                op = self.take().val
                n = Node("postinc", lv=n, d=1 if op=="++" else -1)
            elif self.accept("["):
                idx = self.parse_expr(); self.take("]")
                n = Node("index", base=n, idx=idx)
            elif self.accept(".") or self.accept("->"):
                arrow = self.toks[self.i - 1].val == "->"
                mname = self.take().val
                n = Node("member", base=n, mname=mname, arrow=arrow)
            else: break
        return n
    def parse_primary(self):
        t = self.peek()
        if t.kind == "str":
            self.take()
            addr = self.str_ptr
            self.strings[addr] = t.val + b"\x00"   # C 字符串以 NUL 结尾
            self.str_ptr += len(self.strings[addr]) + 4
            return Node("str", addr=addr)
        if t.kind == "num": self.take(); return Node("num", val=t.val)
        if t.kind == "id":
            name = self.take().val
            if name in ("va_start", "va_arg", "va_end") and self.at("("):
                # 变参内建：va_start(ap) / va_arg(ap, type) / va_end(ap)
                self.take("(")
                ap = self.parse_expr()          # 游标变量名（通常是 var）
                if name == "va_arg":
                    self.take(",")
                    argty = self.parse_type()   # 第二个参数是类型，不是表达式
                    self.take(")")
                    return Node("va_arg", ap=ap, argty=argty)
                self.take(")")
                return Node(name, ap=ap)
            if self.accept("("):
                args = []
                while not self.at(")"):
                    args.append(self.parse_expr())
                    if not self.accept(","): break
                self.take(")")
                return Node("call", name=name, args=args)
            return Node("var", name=name)
        if self.accept("("):
            e = self.parse_expr(); self.take(")"); return e
        raise SyntaxError(f"期望表达式，实际 {t!r}")
    def val_in(self, *vs): return self.peek().val in vs and self.peek().kind == "op"

# ---------------- 代码生成 ----------------
class CodeGen:
    # syscall 内建（asm-generic / RISC-V Linux 编号，见 trirv_vm.py）
    SYSCALLS = {
        "write": 64, "read": 63, "exit": 93, "brk": 214,
        "getcwd": 17, "openat": 56, "close": 57, "lseek": 62,
        "readv": 65, "writev": 66, "newfstatat": 79, "fstat": 80,
        "exit_group": 94, "set_tid_address": 96, "futex": 98,
        "clock_gettime": 113, "sched_yield": 124,
        "rt_sigaction": 134, "rt_sigprocmask": 135, "rt_sigreturn": 139,
        "uname": 160, "getpid": 172, "gettid": 178,
        "munmap": 215, "mmap": 222, "mprotect": 226, "getrandom": 278,
    }
    WORD = 9                   # int/指针 = 9 tryte；char = 1 tryte

    def __init__(self, structs=None):
        self.out = []          # 汇编文本行
        self.lbl = 0
        self.locals = {}       # 当前函数 变量名 -> fp 字节偏移(tryte)
        self.vtypes = {}       # 变量名 -> 类型字符串("int"/"char"/"char*"/...)
        self.nlocals = 0       # 已分配的 tryte 数
        self.loops = []        # (break_label, continue_label)
        self.func_names = set()
        self.func_rets = {}    # 函数名 -> 返回类型
        self.structs = structs or {}   # name -> {"size":N, "members":[...]}
        self.func_ret_ty = ""          # 当前函数返回类型（供 return 生成用）
        self.func_variadic = {}        # 函数名 -> (是否变参, 固定参数个数)
        self.sret_tmp = 0              # 调用点 sret 返回缓冲计数器

    # ---- 类型工具 ----
    # 维度按 C 顺序：[行][列]。int[2][3] 首元素是 int[3]，最左维=2。
    ARR_RE = re.compile(r"^([^\[]+)\[(\d+)\](.*)$")
    def ty_array(self, ty):
        # 以 * 结尾的是"指向数组的指针"，不按数组退化
        if ty.endswith("*"): return None
        return self.ARR_RE.match(ty)
    def ty_elem(self, ty):
        m = self.ty_array(ty); return m.group(1) + m.group(3) if m else None
    def ty_count(self, ty):
        m = self.ty_array(ty); return int(m.group(2)) if m else 0
    def ty_size(self, ty):
        return type_size(ty, self.structs)
    def ty_ptr(self, ty):
        return ty.endswith("*")
    def ty_pointee(self, ty):
        return ty[:-1] if self.ty_ptr(ty) else ty
    def struct_info(self, ty):
        """取结构体类型的信息表；ty 形如 'struct S'。"""
        return self.structs.get(ty[len("struct "):])
    def member_info(self, sty, mname):
        """在结构体类型中查成员 (mtype, offset)。"""
        info = self.struct_info(sty)
        if not info: raise SyntaxError(f"非结构体类型成员访问: {sty}")
        for mn, mt, off in info["members"]:
            if mn == mname: return mt, off
        raise SyntaxError(f"结构体无成员: {mname}")

    def new_label(self, tag="L"):
        self.lbl += 1; return f"_{tag}{self.lbl}"

    def emit(self, s): self.out.append("    " + s)

    # ---- 求值栈（每项 1 个 45-trit 字 = 9 tryte）----
    def push(self, reg):
        self.emit(f"SW   {reg}, 0(sp)")
        self.emit(f"ADDI sp, sp, {self.WORD}")
    def pop(self, reg):
        self.emit(f"ADDI sp, sp, -{self.WORD}")
        self.emit(f"LW   {reg}, 0(sp)")

    # ---- 按类型访存：char 用 LB/SB(1 tryte)，其余用 LW/SW(WORD_TRYTES) ----
    def _mem_addr(self, base, off):
        """返回可安全用作 (off) 的基址寄存器：偏移超 7-trit 有符号范围时用 t3 换算。
        访存偏移字段是 7-trit（±1093），局部变量区较大时绝对偏移会溢出并被
        VM sign_extend 解成负数——必须先算出 base+off 的地址再以 0 偏移访存。"""
        HALF7 = (3**7 - 1) // 2
        if -HALF7 <= off <= HALF7:
            return base, off
        assert base != "t3", "t3 与基址冲突"
        self.load_imm("t3", off)
        self.emit(f"ADD  t3, t3, {base}")
        return "t3", 0

    def load_ty(self, ty, base, off, dst):
        b, o = self._mem_addr(base, off)
        # char 无符号（RISC-V psABI 默认），走 LBU 零扩展；仅 signed char 用 LB 符号扩展。
        op = "LBU" if ty in ("char", "uchar") else ("LB" if ty == "schar" else "LW")
        self.emit(f"{op}   {dst}, {o}({b})")
    def store_ty(self, ty, base, off, src):
        b, o = self._mem_addr(base, off)
        op = "SB" if ty in ("char", "uchar", "schar") else "SW"
        self.emit(f"{op}   {src}, {o}({b})")

    # ---- 结构体整块拷贝（按字，struct 大小恒为 9 的倍数）----
    def copy_struct(self, ty, dst_base, dst_off, src_base, src_off):
        """把 src_base+src_off 处 struct ty 逐字拷到 dst_base+dst_off。"""
        n = self.ty_size(ty)
        assert n % self.WORD == 0, f"struct {ty} 大小非字宽倍数: {n}"
        for a in range(0, n, self.WORD):
            sb, so = self._mem_addr(src_base, src_off + a)
            self.emit(f"LW   t2, {so}({sb})")
            db, do = self._mem_addr(dst_base, dst_off + a)
            self.emit(f"SW   t2, {do}({db})")

    def load_imm(self, reg, n):
        """把 45-trit 有符号常量装入寄存器（覆盖全 45-trit 范围）。
        按 20-trit 分段，每段用对称有符号表示 seg ∈ [-(3^20-1)/2, (3^20-1)/2]，
        避免普通取模在段上界附近产生 hi 借位溢出 13-trit LUI 字段。"""
        if n == 0:
            self.emit(f"ADDI {reg}, zero, 0")
            return
        BASE = 3**20
        HALF_B = (BASE - 1) // 2
        segs = []
        m = n
        while m:
            seg = m % BASE
            m //= BASE
            if seg > HALF_B:                 # 平衡化：大段取负并向更高段借 1
                seg -= BASE
                m += 1
            segs.append(seg)
        self._load_imm20(reg, segs[-1])        # 最高段（可能为负）
        for seg in reversed(segs[:-1]):
            self.emit(f"SLLI {reg}, {reg}, 20")  # 20 < 3^7，合法 7-trit 移位量
            self._load_imm20("t3", seg)           # t3 空闲（编译器仅用 t0/t1/t2/t6）
            self.emit(f"ADD  {reg}, {reg}, t3")

    def _load_imm20(self, reg, n):
        """装一个 |n| <= (3^20-1)/2 的有符号段：LUI(13 trit) + ADDI(7 trit，带借位)。
        |n| < 3^20/2 保证 lo 借位后 hi 仍落在 13-trit 范围内，不会溢出。"""
        if n < 0:
            self._load_imm20(reg, -n)
            self.emit(f"SUB  {reg}, zero, {reg}")
            return
        hi, lo = divmod(n, 3**7)
        HALF7 = (3**7 - 1) // 2
        if lo > HALF7:
            lo -= 3**7
            hi += 1
        if hi == 0:
            self.emit(f"ADDI {reg}, zero, {lo}")
        else:
            self.emit(f"LUI  {reg}, {hi}")
            if lo: self.emit(f"ADDI {reg}, {reg}, {lo}")

    # ---- 局部变量的静态收集（供 prologue 预知栈帧大小）----
    def scan_decls(self, n, names):
        k = n.kind
        if k == "decl":
            for name, ty, init in n.decls:
                if not any(x[0] == name for x in names): names.append((name, ty))
                if init: self.scan_decls_any(init, names)
        elif k == "block":
            for s in n.stmts: self.scan_decls(s, names)
        elif k == "if":
            self.scan_decls_any(n.cond, names); self.scan_decls(n.then, names)
            if n.els: self.scan_decls(n.els, names)
        elif k in ("while", "for"):
            if k == "for" and n.init: self.scan_decls_any(n.init, names)
            self.scan_decls_any(n.cond if n.cond else Node("num", val=0), names)
            if k == "for" and n.step: self.scan_decls_any(n.step, names)
            self.scan_decls(n.body, names)
        elif k == "expr":
            self.scan_decls_any(n.expr, names)
        elif k == "return":
            self.scan_decls_any(n.expr, names)

    def scan_decls_any(self, e, names):
        if e is None: return
        k = e.kind
        if k == "bin": self.scan_decls_any(e.l, names); self.scan_decls_any(e.r, names)
        elif k in ("un", "addr", "deref"): self.scan_decls_any(e.e, names)
        elif k == "index": self.scan_decls_any(e.base, names); self.scan_decls_any(e.idx, names)
        elif k == "member": self.scan_decls_any(e.base, names)
        elif k == "initlist":
            for it in e.items: self.scan_decls_any(it, names)
        elif k == "assign": self.scan_decls_any(e.lv, names); self.scan_decls_any(e.expr, names)
        elif k in ("preinc", "postinc"): self.scan_decls_any(e.lv, names)
        elif k == "call":
            retty = self.func_rets.get(e.name, "int")
            if is_struct(retty) and not hasattr(e, "sret_slot"):
                slot = f"__sret{self.sret_tmp}"; self.sret_tmp += 1
                e.sret_slot = slot
                names.append((slot, retty))      # 隐藏返回缓冲，随栈帧分配
            for a in e.args: self.scan_decls_any(a, names)

    # ---- 函数帧 ----
    def func_prologue(self, nlocals, params, sret=False, variadic=False):
        # 栈帧布局(tryte): [S]=old fp(9B), [S+9]=ra(9B), fp=S+18, 局部变量在 fp+off
        # 栈参数块布局: 调用方把参数从低到高连续压栈，块基址存 t6；
        #              参数 i 在 t6 + i*WORD。进入被调方时 sp = 块基址 + nargs*WORD，
        #              prologue 抬帧后 sp 跳过局部区，参数块在旧 sp 之下、帧之上，不受影响。
        W = self.WORD
        self.emit("SW   s0, 0(sp)")       # 保存旧 fp（1 字）
        self.emit(f"SW   ra, {W}(sp)")    # 保存返回地址（1 字）
        self.emit(f"ADDI s0, sp, {2*W}")  # fp = sp + 2*W
        # 帧大小可能 > 1093（7-trit 正上限），必须走 load_imm 的 LUI+ADDI 拆分；
        # 否则 ADDI 立即数被 VM sign_extend 解成负数，sp 不升反降踩烂调用者栈帧。
        self.load_imm("t2", nlocals + 2*W)
        self.emit("ADD  sp, sp, t2")      # 求值栈顶跳过帧
        # sret 函数：a0 是隐藏返回缓冲指针，存入 __sretptr 局部槽
        abase = 0
        if sret:
            self.store_ty("int", "s0", self.locals["__sretptr"], "a0")
            abase = 1
        # 变参函数：把参数块基址 t6 存入 __argbase（供 va_start/va_arg 连续读取）
        if variadic:
            self.store_ty("int", "s0", self.locals["__argbase"], "t6")
        # 形参加载：前 (8-ab) 个从寄存器；第 9+ 个从 t6 参数块
        nreg = 8 - abase
        for i, (p, _ty) in enumerate(params):
            ty = self.vtypes[p]
            if i < nreg:
                ai = f"a{i + abase}"
                if is_struct(ty):
                    self.copy_struct(ty, "s0", self.locals[p], ai, 0)
                else:
                    self.store_ty(ty, "s0", self.locals[p], ai)
            else:
                p_off = i * W
                if is_struct(ty):
                    self.copy_struct(ty, "s0", self.locals[p], "t6", p_off)
                else:
                    self.emit(f"LW   t2, {p_off}(t6)")
                    self.store_ty(ty, "s0", self.locals[p], "t2")

    def func_epilogue(self):
        W = self.WORD
        self.emit(f"LW   ra, {-W}(s0)")   # ra 在 fp-W
        self.emit(f"ADDI sp, s0, {-2*W}") # sp 回到 S
        self.emit("LW   s0, 0(sp)")       # 恢复旧 fp
        self.emit("JALR x0, 0(ra)")

    # ---- 表达式：gen_expr 把值推到求值栈，返回该值的类型 ----
    def gen_expr(self, n):
        k = n.kind
        if k == "num":
            self.load_imm("t0", n.val); self.push("t0"); return "int"
        elif k == "str":
            self.load_imm("t0", n.addr); self.push("t0"); return "char*"  # 字符串首址
        elif k == "var":
            ty = self.vtypes[n.name]
            if self.ty_array(ty):
                # 数组名退化为指向首元素的指针：压入首址，返回元素指针类型
                self.load_imm("t0", self.locals[n.name])
                self.emit("ADD  t0, t0, s0")
                self.push("t0"); return self.ty_elem(ty) + "*"
            if is_struct(ty):
                # struct 右值 = 首地址（不做单字 load）
                self.load_imm("t0", self.locals[n.name])
                self.emit("ADD  t0, t0, s0")
                self.push("t0"); return ty
            self.load_ty(ty, "s0", self.locals[n.name], "t0"); self.push("t0"); return ty
        elif k == "addr":
            # &x：取左值地址（gen_addr 把地址推栈），结果是指向该类型的指针
            ety = self.gen_addr(n.e)
            return ety + "*"
        elif k == "deref":
            # *p：先求指针值（在栈顶=地址），再按 pointee 类型 load
            pty = self.gen_expr(n.e)
            self.pop("t0")                       # t0 = 地址
            ety = self.ty_pointee(pty)
            if is_struct(ety):                   # struct 右值 = 该地址
                self.push("t0"); return ety
            self.load_ty(ety, "t0", 0, "t0"); self.push("t0"); return ety
        elif k == "index":
            # a[i]：gen_addr(index) 压入元素地址并返回元素类型
            ety = self.gen_addr(n)
            self.pop("t0")
            if self.ty_array(ety):
                # 多维数组内层下标：元素是数组，保留地址并退化为元素指针
                self.push("t0"); return self.ty_elem(ety) + "*"
            if is_struct(ety):                   # struct 数组元素右值 = 元素地址
                self.push("t0"); return ety
            self.load_ty(ety, "t0", 0, "t0"); self.push("t0"); return ety
        elif k == "member":
            # s.x / p->x：gen_addr(member) 压入成员地址并返回成员类型
            mty = self.gen_addr(n)
            self.pop("t0")
            if is_struct(mty):                   # struct 成员右值 = 成员地址
                self.push("t0"); return mty
            self.load_ty(mty, "t0", 0, "t0"); self.push("t0"); return mty
        elif k == "assign":
            lty = self.gen_addr(n.lv)            # 栈顶 = 左值地址
            self.gen_expr(n.expr); self.pop("t1")  # t1 = 右值（struct 时=源地址）
            self.pop("t0")                       # t0 = 目的地址
            if is_struct(lty):
                self.copy_struct(lty, "t0", 0, "t1", 0)
                self.push("t0"); return lty       # 赋值结果 = 目的 struct 地址
            self.store_ty(lty, "t0", 0, "t1")
            self.push("t1"); return lty
        elif k == "un":
            ty = self.gen_expr(n.e); self.pop("t0")
            if n.op == "-": self.emit("SUB  t0, zero, t0")
            elif n.op == "+": pass
            elif n.op == "!":
                self.emit("SLTIU t0, t0, 1")
            elif n.op == "~": self.emit("TNOT t0, t0")
            self.push("t0"); return "int" if ty != "char*" else ty
        elif k in ("preinc", "postinc"):
            lty = self.gen_addr(n.lv)            # 栈顶 = 左值地址
            self.pop("t0")                       # t0 = 地址
            self.load_ty(lty, "t0", 0, "t1")     # t1 = 旧值
            step = 1 if lty in ("char","uchar","schar") else (self.ty_size(self.ty_pointee(lty)) if self.ty_ptr(lty) else 1)
            step *= n.d
            self.load_imm("t2", step)
            self.emit("ADD  t1, t1, t2")         # 新值
            self.store_ty(lty, "t0", 0, "t1")
            if k == "preinc": self.push("t1")
            else:
                self.emit("SUB  t1, t1, t2")     # 返回旧值
                self.push("t1")
            return lty
        elif k == "bin":
            op = n.op
            if op in ("&&", "||"):
                # C 短路语义：左边即可定结果时不求值右边（如 p && *p）。
                self.gen_expr(n.l); self.pop("t0")
                self.emit("SLTU t0, zero, t0")   # bool(x) = (x != 0)  # t0 = bool(l)
                short = self.new_label("short"); end = self.new_label("logic")
                # && 左假 -> 短路得 0；|| 左真 -> 短路得 1
                self.emit(f"{'BEQ' if op=='&&' else 'BNE'} t0, zero, {short}")
                self.gen_expr(n.r); self.pop("t1")
                self.emit("SLTU t1, zero, t1")  # t1 = bool(r)
                self.emit("ADDI t0, t1, 0")                                 # 结果 = bool(r)
                self.emit(f"JAL x0, {end}")
                self.out.append(short + ":")
                self.emit(f"ADDI t0, zero, {0 if op=='&&' else 1}")
                self.out.append(end + ":")
                self.push("t0"); return "int"
            lty = self.gen_expr(n.l); rty = self.gen_expr(n.r)
            self.pop("t1"); self.pop("t0")
            # 指针 - 指针：差值 / 元素大小（必须先判，否则会被下面的"指针±整数"误命中）
            if op == "-" and self.ty_ptr(lty) and self.ty_ptr(rty):
                sc = self.ty_size(self.ty_pointee(lty))
                self.emit("SUB  t0, t0, t1")
                if sc != 1:
                    self.load_imm("t2", sc); self.emit("DIV  t0, t0, t2")
                self.push("t0"); return "int"
            # 指针 ± 整数：按 pointee 大小缩放整数
            if op in ("+", "-") and (self.ty_ptr(lty) or self.ty_ptr(rty)):
                if self.ty_ptr(lty):
                    sc = self.ty_size(self.ty_pointee(lty)); pty = lty
                    if sc != 1:
                        self.load_imm("t2", sc * (1 if op == "+" else -1))
                        self.emit("MUL  t1, t1, t2")
                    self.emit("ADD  t0, t0, t1")  # 加上缩放后的偏移（减法已并入缩放符号）
                else:  # 整数 + 指针（仅 + 有意义）
                    sc = self.ty_size(self.ty_pointee(rty))
                    if sc != 1:
                        self.load_imm("t2", sc); self.emit("MUL  t0, t0, t2")
                    self.emit("ADD  t0, t0, t1"); pty = rty
                self.push("t0"); return pty
            # C 通常算术转换：任一操作数为 unsigned -> 整式按无符号运算
            uns = is_unsigned(lty) or is_unsigned(rty)
            self.gen_bin(op, uns); self.push("t0")
            return "unsigned" if uns and op in ("/", "%") else "int"
        elif k == "va_start":
            # ap = 参数块基址 + (固定参数个数)*WORD = 第一个变参地址
            # 参数块连续存放所有实参（含跨寄存器/栈边界），va_arg 按字递增即可
            nfixed = self.func_variadic.get(self.cur_func, (False, 0))[1]
            self.gen_addr(n.ap); self.pop("t0")       # t0 = ap 变量槽地址
            ab_off = self.locals["__argbase"]
            self.emit(f"LW   t1, {ab_off}(s0)")       # t1 = __argbase 槽值 = 参数块基址
            off = nfixed * self.WORD
            if off:
                self.emit(f"ADDI t1, t1, {off}")
            self.emit("SW   t1, 0(t0)")               # 写入 ap 游标
            self.load_imm("t0", 0); self.push("t0"); return "int"
        elif k == "va_arg":
            self.gen_addr(n.ap); self.pop("t0")       # t0 = ap 变量槽地址
            self.emit("LW   t1, 0(t0)")               # t1 = 当前变参地址
            self.load_ty(n.argty, "t1", 0, "t2")      # t2 = 参数值
            self.emit(f"ADDI t1, t1, {self.WORD}")    # 游标前进一字
            self.emit("SW   t1, 0(t0)")
            self.push("t2"); return n.argty
        elif k == "va_end":
            self.load_imm("t0", 0); self.push("t0"); return "int"  # 空操作
        elif k == "call":
            retty = self.func_rets.get(n.name, "int")
            sret = is_struct(retty)
            abase = 1 if sret else 0            # sret 占用 a0 作返回缓冲
            REG = 8 - abase                     # 可用参数寄存器数
            nargs = len(n.args)
            is_var = self.func_variadic.get(n.name, (False, 0))[0]
            # 变参一律走栈块（va_start 需连续参数区）；普通函数仅参数超寄存器时走栈块
            need_stack = is_var or (nargs > REG)
            if n.name in self.SYSCALLS:
                # syscall：参数直接走 a0..（无 sret、无栈块）
                for a in n.args: self.gen_expr(a)
                for i in range(nargs - 1, -1, -1): self.pop(f"a{i}")
                self.load_imm("a7", self.SYSCALLS[n.name])
                self.emit("ECALL")
                self.push("a0")
                return retty
            for a in n.args: self.gen_expr(a)   # 求值全部实参到栈（struct 实参压入首址）
            if need_stack:
                # t6 = 参数块基址 SP0 = 当前 sp - nargs*WORD
                self.emit(f"ADDI t6, sp, {-nargs * self.WORD}")
                for i in range(min(nargs, REG)):  # 前 REG 个从块搬到寄存器
                    self.emit(f"LW   a{i + abase}, {i * self.WORD}(t6)")
            else:
                for i in range(nargs - 1, -1, -1): self.pop(f"a{i + abase}")
            if sret:
                self.load_imm("t0", self.locals[n.sret_slot])
                self.emit("ADD  t0, t0, s0")
                self.emit("ADD  a0, zero, t0")
            self.emit(f"JAL  ra, {n.name}")
            if need_stack:
                self.emit(f"ADDI sp, sp, {-nargs * self.WORD}")  # 回收参数块
            if sret:
                self.load_imm("t0", self.locals[n.sret_slot])
                self.emit("ADD  t0, t0, s0")
                self.push("t0")             # 结果 = 返回缓冲地址
            else:
                self.push("a0")
            return retty
        else:
            raise RuntimeError(f"未知表达式节点 {k}")

    # ---- 左值地址：把"地址"推到求值栈，返回被指向类型 ----
    def gen_addr(self, n):
        k = n.kind
        if k == "var":
            self.load_imm("t0", self.locals[n.name])
            self.emit("ADD  t0, t0, s0")         # 地址 = fp + 偏移
            self.push("t0")
            return self.vtypes[n.name]
        elif k == "deref":
            # *p 的地址就是 p 的值
            pty = self.gen_expr(n.e)             # 栈顶 = 指针值 = 地址
            return self.ty_pointee(pty)
        elif k == "index":
            aty = self.gen_expr(n.base)          # 基址（指针）
            self.gen_expr(n.idx)                 # 索引
            self.pop("t1"); self.pop("t0")       # t0=base, t1=idx
            ety = self.ty_pointee(aty)
            sc = self.ty_size(ety)
            if sc != 1:
                self.load_imm("t2", sc); self.emit("MUL  t1, t1, t2")
            self.emit("ADD  t0, t0, t1")         # 元素地址
            self.push("t0")
            return ety
        elif k == "member":
            if n.arrow:
                # p->x：p 是指针，其值为结构体首址
                bty = self.gen_expr(n.base)      # 压入指针值，bty=struct S*
                sty = self.ty_pointee(bty)
            elif n.base.kind in ("var", "member", "index", "deref"):
                # s.x：s 是可取址的结构体左值，gen_addr 压入其首址
                sty = self.gen_addr(n.base)
            else:
                # f().x / (表达式).x：base 是结构体右值，其值本身就是首址（sret 缓冲）
                sty = self.gen_expr(n.base)
            mty, off = self.member_info(sty, n.mname)
            if off:
                self.pop("t0")
                self.load_imm("t1", off)
                self.emit("ADD  t0, t0, t1")
                self.push("t0")
            return mty
        else:
            raise SyntaxError(f"不可作为左值: {k}")

    def gen_bin(self, op, uns=False):
        # 三进制 ISA 的 AND/OR/XOR 是逐 trit 三值逻辑，不是二进制按位。
        # C 的 & | ^ 要二进制语义，只能软件模拟（尚未实现）—— 必须硬报错，
        # 静默映射会让 x&1 算成三值 min，比编译失败危险得多。
        if op in ("&", "|", "^"):
            raise RuntimeError(
                f"UNSUPPORTED: 二进制位运算 {op} —— 方案 A（精确 72 位视图）已在 C 后端实现"
                f"（见 trirv_bitview_e2e.py），Python 参考后端尚未跟进。"
                f"这里继续拒绝而不是拿三值 min/max 顶替，避免两套后端语义漂移")
        # ISA 的 SLL/SRL/SRA 是三进制移位（×3^k / ÷3^k），C 的 << >> 是二进制（×2^k）。
        # 直接映射会让 x<<1 静默得到 ×3（实测 x=10 得 30，而 C 语义要求 20）。
        if op in ("<<", ">>"):
            raise RuntimeError(
                f"UNSUPPORTED: 移位运算 {op} —— 二进制移位适配层已在 C 后端实现"
                f"（见 trirv_bitview_e2e.py 的移位段），Python 参考后端尚未跟进。"
                f"这里继续拒绝而不是直译成 ISA 的三进制 SLL/SRL（×3^k），"
                f"因为那会让 x<<1 静默变成 ×3")
        simple = {"+":"ADD","-":"SUB","*":"MUL","/":"DIV","%":"REM"}
        if op == "/" and uns: self.emit("DIVU t0, t0, t1"); return
        if op == "%" and uns: self.emit("REMU t0, t0, t1"); return
        if op in simple:
            self.emit(f"{simple[op]}  t0, t0, t1"); return
        rel = {"<":"SLT", "<=":"SGT", ">":"SGT", ">=":"SLT", "==":"SEQ", "!=":"SNE"}
        if op in rel:
            lt = "SLTU" if uns else "SLT"
            if op in ("<", ">="):
                self.emit(f"{lt} t0, t0, t1")
                if op == ">=": self.emit("SLTIU t0, t0, 1")  # !(a<b)
            elif op in (">", "<="):
                self.emit(f"{lt} t0, t1, t0")
                if op == "<=": self.emit("SLTIU t0, t0, 1")  # !(a>b)
            elif op == "==":
                self.emit("SUB  t0, t0, t1")
                self.emit("SLTIU t0, t0, 1")   # diff==0 -> 1
            elif op == "!=":
                self.emit("SUB  t0, t0, t1")
                self.emit("SLTU t0, zero, t0")
            return
        if op == "&&":
            self.emit("SLTU t0, zero, t0")   # bool(x) = (x != 0)
            self.emit("SLTU t1, zero, t1")
            self.emit("AND  t0, t0, t1"); return
        if op == "||":
            self.emit("SLTU t0, zero, t0")   # bool(x) = (x != 0)
            self.emit("SLTU t1, zero, t1")
            self.emit("OR   t0, t0, t1"); return
        raise RuntimeError(f"未知二元运算 {op}")

    # ---- 语句 ----
    # ---- 初始化辅助 ----
    def emit_zero(self, ty, base, off):
        """把 base+off 起始、类型 ty 的区域清零（不越界踩相邻局部变量）。"""
        total = self.ty_size(ty)
        self.load_imm("t0", 0)
        step = self.WORD if total % self.WORD == 0 else 1
        op = "SW" if step == self.WORD else "SB"
        # 逐元素换算地址：off+a 可能超 7-trit，必须走 _mem_addr
        for a in range(0, total, step):
            b, o = self._mem_addr(base, off + a)
            self.emit(f"{op}  t0, {o}({b})")

    def iter_leaves(self, ty, base_off):
        """按内存顺序产出复合类型的叶子标量 (类型, 偏移)。"""
        if self.ty_array(ty):
            elem = self.ty_elem(ty); esz = self.ty_size(elem)
            for i in range(self.ty_count(ty)):
                yield from self.iter_leaves(elem, base_off + i * esz)
        elif is_struct(ty):
            for _mn, mt, mo in self.struct_info(ty)["members"]:
                yield from self.iter_leaves(mt, base_off + mo)
        else:
            yield (ty, base_off)

    def flatten_init(self, node):
        """把初始化列表（含嵌套）展平为标量表达式节点列表。"""
        if node.kind == "initlist":
            out = []
            for it in node.items: out += self.flatten_init(it)
            return out
        return [node]

    def emit_init(self, ty, base, off, init):
        """把初始化器写入类型区域；先整块清零，再按内存顺序填值。"""
        self.emit_zero(ty, base, off)
        if init is None: return
        if is_struct(ty) and init.kind != "initlist":
            # struct 来源（变量/返回 struct 的调用）：gen_expr 压入源地址，逐字拷贝
            self.gen_expr(init); self.pop("t0")
            self.copy_struct(ty, base, off, "t0", 0)
            return
        if self.ty_array(ty) and init.kind != "initlist":
            raise SyntaxError("数组初始化需要 {..}")
        vals = self.flatten_init(init)
        for (lt, lo), v in zip(self.iter_leaves(ty, off), vals):
            self.gen_expr(v); self.pop("t0")
            self.store_ty(lt, base, lo, "t0")

    def gen_stmt(self, n):
        k = n.kind
        if k == "block":
            for s in n.stmts: self.gen_stmt(s)
        elif k == "decl":
            for name, ty, init in n.decls:
                off = self.locals[name]   # 已在编译前静态分配（tryte 偏移）
                self.emit_init(ty, "s0", off, init)
        elif k == "expr":
            self.gen_expr(n.expr); self.pop("t0")
        elif k == "if":
            els = self.new_label("else"); end = self.new_label("end")
            self.gen_expr(n.cond); self.pop("t0")
            self.emit(f"BEQ  t0, zero, {els if n.els else end}")
            self.gen_stmt(n.then)
            if n.els:
                self.emit(f"JAL  x0, {end}")
                self.out.append(els + ":")
                self.gen_stmt(n.els)
            self.out.append(end + ":")
        elif k == "while":
            start = self.new_label("while"); end = self.new_label("end")
            self.out.append(start + ":")
            self.gen_expr(n.cond); self.pop("t0")
            self.emit(f"BEQ  t0, zero, {end}")
            self.loops.append((end, start))
            self.gen_stmt(n.body)
            self.loops.pop()
            self.emit(f"JAL  x0, {start}")
            self.out.append(end + ":")
        elif k == "for":
            # 结构: init; start: cond?; body; cont: step; JAL start; end:
            if n.init: self.gen_stmt(Node("expr", expr=n.init))
            start = self.new_label("for"); end = self.new_label("end")
            cont = self.new_label("cont")
            self.out.append(start + ":")
            if n.cond:
                self.gen_expr(n.cond); self.pop("t0")
                self.emit(f"BEQ  t0, zero, {end}")
            self.loops.append((end, cont))
            self.gen_stmt(n.body)
            self.out.append(cont + ":")            # continue 跳到这里执行 step
            if n.step: self.gen_stmt(Node("expr", expr=n.step))
            self.emit(f"JAL  x0, {start}")
            self.loops.pop()
            self.out.append(end + ":")
        elif k == "return":
            if is_struct(self.func_ret_ty):
                # struct 返回：求值得到源地址(t0)，逐字拷入 __sretptr 指向的缓冲
                self.gen_expr(n.expr); self.pop("t0")     # t0 = 源 struct 地址
                self.load_ty("int", "s0", self.locals["__sretptr"], "t1")  # t1 = 缓冲地址
                self.copy_struct(self.func_ret_ty, "t1", 0, "t0", 0)
                self.emit("ADD  a0, zero, t1")            # 返回缓冲地址（约定）
            else:
                self.gen_expr(n.expr); self.pop("a0")
            self.func_epilogue()
        elif k == "break":
            if not self.loops: raise SyntaxError("break 不在循环内")
            self.emit(f"JAL  x0, {self.loops[-1][0]}")
        elif k == "continue":
            if not self.loops: raise SyntaxError("continue 不在循环内")
            self.emit(f"JAL  x0, {self.loops[-1][1]}")
        else:
            raise RuntimeError(f"未知语句 {k}")

    def compile(self, fns):
        # 先收集函数名与返回类型
        self.func_names = {f.name for f in fns}
        self.func_rets = {f.name: f.ret for f in fns}
        self.func_variadic = {f.name: (getattr(f, "variadic", False), len(f.params))
                              for f in fns}
        # _start: 调用 main，然后 ECALL 退出
        self.out.append("_start:")
        self.emit("JAL  ra, main")
        self.emit("ADDI a7, zero, 93")     # exit syscall
        self.emit("ECALL")
        for f in fns:
            self.out.append(f"{f.name}:")
            self.func_ret_ty = f.ret
            self.cur_func = f.name
            variadic = getattr(f, "variadic", False)
            # 收集局部变量：参数(name,ty) + 函数体内所有 decl(name,ty)，去重保序
            names = list(f.params)
            self.scan_decls(f.body, names)
            if is_struct(f.ret):
                names.insert(0, ("__sretptr", "int"))    # 隐藏返回缓冲指针槽
            if variadic:
                names.insert(0, ("__argbase", "int")) # 参数块基址（va_start 用）
            seen = set(); self.locals = {}; self.vtypes = {}; self.nlocals = 0
            for nm, ty in names:
                if nm not in seen:
                    self.vtypes[nm] = ty
                    self.locals[nm] = self.nlocals  # tryte 偏移
                    self.nlocals += self.ty_size(ty)
                    seen.add(nm)
            self.func_prologue(self.nlocals, f.params, sret=is_struct(f.ret),
                               variadic=variadic)
            self.gen_stmt(f.body)
            # 若末尾未 return，补默认返回
            if is_struct(f.ret):
                # 无显式 return：把返回缓冲整块清零
                self.load_ty("int", "s0", self.locals["__sretptr"], "t1")
                self.load_imm("t2", 0)
                sz = self.ty_size(f.ret)
                for a in range(0, sz, self.WORD):
                    b, o = self._mem_addr("t1", a)
                    self.emit(f"SW   t2, {o}({b})")
                self.emit("ADD  a0, zero, t1")
            else:
                self.load_imm("a0", 0)
            self.func_epilogue()
        return "\n".join(self.out) + "\n"

# ---------------- 顶层接口 ----------------
def compile_c(src):
    """C 源码 -> (机器码, 符号表, 汇编文本, 字符串常量表 {addr: bytes})。"""
    toks = lex(src)
    parser = Parser(toks)
    fns = parser.parse_program()
    cg = CodeGen(parser.structs)
    asm_text = cg.compile(fns)
    prog, labels = asm.assemble(asm_text)
    return prog, labels, asm_text, parser.strings
