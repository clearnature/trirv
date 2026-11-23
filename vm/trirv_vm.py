"""TriRV VM —— 取指/译码/执行骨架。

- 32 个 45-trit 寄存器（4-trit 字段，对齐 RISC-V x0-x31）
- 指令内存按"指令槽"编址（简化：每条 20-trit 指令占 1 槽）
- 数据内存：byte(tryte) 编址，dict 存储
"""
import trirv_isa as isa
import trirv_float as tf

XLEN = isa.XLEN
MOD = isa.MOD_X
HALF = isa.HALF_X

def s(v):            # 无符号 -> 有符号
    return isa.to_signed_word(v)
def u(v):            # 有符号 -> 无符号
    return isa.from_signed_word(v)

# Syscall 编号（asm-generic / RISC-V Linux 约定，见 include/uapi/asm-generic/unistd.h）
SYS_GETCWD       = 17
SYS_OPENAT       = 56
SYS_CLOSE        = 57
SYS_LSEEK        = 62
SYS_READ         = 63
SYS_WRITE        = 64
SYS_READV        = 65
SYS_WRITEV       = 66
SYS_FSTATAT      = 79
SYS_FSTAT        = 80
SYS_EXIT         = 93
SYS_EXIT_GROUP   = 94
SYS_SET_TID_ADDR = 96
SYS_FUTEX        = 98
SYS_CLOCK_GETTIME= 113
SYS_SCHED_YIELD  = 124
SYS_RT_SIGACTION = 134
SYS_RT_SIGPROCMASK=135
SYS_RT_SIGRETURN = 139
SYS_UNAME        = 160
SYS_GETPID       = 172
SYS_GETTID       = 178
# ---- TriRV 扩展区：8-bit 视图（octet <-> 双 tryte [lo=o%243, hi=o/243]）----
SYS_READ8        = 5000
SYS_WRITE8       = 5001
SYS_MUNMAP       = 215
SYS_MMAP         = 222
SYS_MPROTECT     = 226
SYS_BRK          = 214
SYS_GETRANDOM    = 278

# 错误码（负返回值，Linux 约定 errno 取负）
EBADF = -9; ENOMEM = -12; EINVAL = -22; ENOSYS = -38; ENOENT = -2

SYS_NAME = {
    17: "getcwd", 56: "openat", 57: "close", 62: "lseek",
    63: "read", 64: "write", 65: "readv", 66: "writev",
    79: "newfstatat", 80: "fstat", 93: "exit", 94: "exit_group",
    96: "set_tid_address", 98: "futex", 113: "clock_gettime", 124: "sched_yield",
    134: "rt_sigaction", 135: "rt_sigprocmask", 139: "rt_sigreturn",
    160: "uname", 172: "getpid", 178: "gettid", 214: "brk",
    215: "munmap", 222: "mmap", 226: "mprotect", 278: "getrandom",
}

class VM:
    WORD_TRYTES = 9                    # int = 45 trit = 9 tryte（每 tryte 5 trit）

    def __init__(self, program, mem_init=None, stdin=b"", argv=None):
        self.regs = [0] * isa.NREG
        # F 扩展浮点寄存器堆：f0..f31，存 TF45 的 45-trit raw（= (sign,mant,exp3)
        # 分离表示的无损打包）。裸 double 会把指数卡在 3^646，违背全阶码模型；
        # raw 打包天然无损，算术时才 decompose → mant 进 FPU、exp3 走整数 → compose。
        self.freg = [0] * 32
        self.imem = list(program)             # 指令槽
        # 统一内存：tryte 编址，每格存 1 tryte(0..242)；bytearray 容量足够
        self.mem = bytearray(1 << 20)
        if mem_init:
            for addr, data in mem_init.items():
                for k, ch in enumerate(data):
                    if 0 <= addr + k < len(self.mem):
                        self.mem[addr + k] = ch % 243
        self.stdin = bytearray(stdin)         # read() 的输入源
        self.output = bytearray()             # write() 的输出（stdout）
        self.exit_code = 0
        self.heap_base = 1 << 19              # 堆起始 tryte 地址（收缩下限）
        self.brk_ptr = self.heap_base         # brk 堆顶（内存中段）
        self.pc = 0
        self.halted = False
        self.steps = 0
        self.regs[2] = 4096                   # sp: tryte 地址，向高地址增长
        self.syscalls = []                    # 记录 (号, a0, a1, a2) 历史
        # --- glibc 启动所需的最小进程状态 ---
        self.pid = 1                          # getpid() 返回值（单进程 VM）
        self.tid = 1                          # gettid() / set_tid_address()
        self.clear_tid_addr = 0               # set_tid_address 记录的清除地址
        self.mmap_ptr = (1 << 20) - (1 << 16) # mmap 区域从高位向下分配，与 brk 反向
        self.clock_ns = 0                     # clock_gettime 单调时钟（每次 +1ms）
        self.rng_state = 0x12345678           # getrandom 确定性 PRNG 状态
        self.open_files = {}                  # fd -> 名称（openat 分配）
        self.next_fd = 3                      # 0/1/2 保留给 stdin/out/err
        # --- Zicsr + 特权态（最小集）---
        self.priv = 3                         # 当前特权级：0=U, 3=M；复位为 M（向后兼容）
        self.csr = {addr: 0 for addr in isa.CSR.values()}   # CSR 寄存器文件
        self.csr[isa.CSR["mstatus"]] = 0
        self.irq_timer = False                # 定时器中断挂起位（供测试/固件注入）
        # --- A 扩展：LR/SC 保留集（单线程 VM：记录地址 + 有效性）---
        self.lr_addr = -1
        self.lr_valid = False
        self.trap_log = []                    # (cause, from_pc, to_pc) 陷入历史
        # --- crt0 进程信息（零页固定地址，与 C VM 严格一致）---
        self._setup_argv(argv or [])

    def _store_word(self, addr, val):
        """写 1 个 45-trit 字 = 9 tryte，小端（低 tryte 在低地址）。"""
        val %= MOD
        for i in range(self.WORD_TRYTES):
            t = (val // (3 ** (5 * i))) % 243   # 每 tryte = 5 trit, 0..242
            if 0 <= addr + i < len(self.mem):
                self.mem[addr + i] = t

    def _setup_argv(self, args):
        """零页：argc@0 argv数组地址@9 envp@18；参数串在 mmap 区之下高地址带。
        与 trirv_vm.c 的 vm_init_ex 保持字节级一致（差分测试依赖）。
        注意：args 为空时【完全不写内存】—— 零页可能被数据段占用
        （wasm hello 的 data 段从 0 开始），写了会把段数据清掉。"""
        gargc = len(args)
        if gargc == 0:
            return
        base = self.mmap_ptr - 16
        arr = ((base - (gargc + 1) * 9) // 9) * 9
        self._store_word(0, gargc)
        self._store_word(9, arr)
        self._store_word(18, 0)
        sp = ((arr - gargc * 9 * 12) // 9) * 9
        for i, s in enumerate(args):
            data = s.encode("latin1") if isinstance(s, str) else bytes(s)
            need = ((len(data) + 1 + 8) // 9) * 9     # 串+NUL，9 对齐
            if sp - need < 4096:
                break
            sentry = sp - need                        # 先减后写：串占 [sentry,sp)
            self._store_word(arr + i * 9, sentry)
            for k, ch in enumerate(data):
                self.mem[sentry + k] = ch % 243
            self.mem[sentry + len(data)] = 0
            sp = sentry
        self._store_word(arr + gargc * 9, 0)     # argv[argc] = NULL

    # ---- 统一内存：按宽度读写（tryte 为单位）----
    def _load_word(self, addr):
        val = 0
        for i in range(self.WORD_TRYTES):
            t = self.mem[addr + i] if 0 <= addr + i < len(self.mem) else 0
            val += (t % 243) * (3 ** (5 * i))
        return val % MOD

    def _store_tryte(self, addr, val):
        if 0 <= addr < len(self.mem):
            self.mem[addr] = val % 243

    def _load_tryte(self, addr):
        return self.mem[addr] % 243 if 0 <= addr < len(self.mem) else 0

    def _overlap(self, addr, size, reserved):
        """写入 [addr,addr+size) 是否与保留字 [reserved,reserved+9) 重叠。"""
        return reserved >= 0 and addr < reserved + 9 and reserved < addr + size

    # ---- Zicsr + 特权态辅助 ----
    def _csr_read(self, addr):
        """读 CSR：mtvec/mepc 等存的是 tryte 地址（指令 i 地址 = 4*i）；未知 CSR 返回 0。"""
        return self.csr.get(addr, 0)

    def _csr_write(self, addr, val):
        """写 CSR：只读位忽略；mtvec/mepc 为 tryte 地址，写入时规整到 4 的倍数。"""
        if addr not in self.csr:
            return                              # 未实现 CSR：写入忽略（RISC-V 允许）
        if addr == isa.CSR["mtvec"] or addr == isa.CSR["mepc"]:
            val = s(u(val))                     # 规整到有符号字
            val = (val // 4) * 4                # PC 是 tryte 地址：4 对齐（下一条 = +4）
        self.csr[addr] = u(val) % MOD
        if addr == isa.CSR["mip"] and (u(val) & (1 << 7)):
            self.irq_timer = True               # 写 mip.MTIP -> 挂起定时器中断

    def _take_trap(self, cause, tval, from_pc):
        """同步/异步陷入：保存现场到 mepc/mcause/mtval，关中断，跳 mtvec。"""
        # mstatus: MPIE<-MIE; MIE<-0; MPP<-当前 priv
        old = self.csr[isa.CSR["mstatus"]]
        new = old & ~isa.MSTATUS_MIE
        if old & isa.MSTATUS_MIE:
            new |= isa.MSTATUS_MPIE
        new &= ~(0x3 << isa.MSTATUS_MPP_SHIFT)
        new |= (self.priv & 0x3) << isa.MSTATUS_MPP_SHIFT
        self.csr[isa.CSR["mstatus"]] = new % MOD
        self._csr_write(isa.CSR["mepc"], from_pc)
        self._csr_write(isa.CSR["mcause"], cause)
        self._csr_write(isa.CSR["mtval"], tval)
        self.priv = 3                           # 陷入总是进入 M 态
        target = s(self.csr[isa.CSR["mtvec"]])
        if target % 4 == 0 and 0 <= target // 4 < len(self.imem):
            self.pc = target                    # mtvec 是 tryte 地址，//4 得指令槽
        else:
            self.halted = True                  # 未设置有效 trap 向量 -> 停机
        self.trap_log.append((s(u(cause)), from_pc, self.pc))

    def _exec_c(self, cd, old_pc):
        """TC 压缩格式执行（T10c-1）：slot 内嵌 C 语义。
        C 指令占满 4-tryte slot，但只用低 15 trit 编码。寄存器窗口 =
        静态子集映射表 _C_REGS。分支跳转基于 old_pc（与全宽口径一致）。"""
        sf = cd["subformat"]; rs = cd["reg_s"]; imm = cd["imm7"]
        _C_REGS = (10, 11, 12, 13, 8, 9, 14, 15)  # t0-t3, s0-s1, a0-a1
        rd = _C_REGS[rs % 8] if rs < 8 else 0
        if sf == 0:      # C-ADDI rd, imm7
            self.regs[rd] = u(s(self.regs[rd]) + isa.sign_extend_imm(imm))
        elif sf == 1:    # C-LW rd, imm7(base=rd 自身)
            addr = s(self.regs[rd]) + isa.sign_extend_imm(imm)
            self.regs[rd] = self._load_word(addr)
        elif sf == 2:    # C-SW src, imm7(base=rd 自身)
            addr = s(self.regs[rd]) + isa.sign_extend_imm(imm)
            self._store_word(addr, self.regs[rd])
        elif sf == 3:    # C-BEQ rs, imm7 → BEQ rs, zero, imm7（old_pc 相对）
            if s(self.regs[rd]) == 0:
                self.pc = old_pc + 4 * isa.sign_extend_imm(imm)
        elif sf == 4:    # C-BNE rs, imm7
            if s(self.regs[rd]) != 0:
                self.pc = old_pc + 4 * isa.sign_extend_imm(imm)

    def _mret(self):
        """从 M 态 trap 返回：pc<-mepc, priv<-MPP, MIE<-MPIE, MPIE<-1, MPP<-U。"""
        mstatus = self.csr[isa.CSR["mstatus"]]
        self.priv = (mstatus >> isa.MSTATUS_MPP_SHIFT) & 0x3
        new = mstatus & ~(0x3 << isa.MSTATUS_MPP_SHIFT)   # MPP <- U(0)
        if mstatus & isa.MSTATUS_MPIE:
            new |= isa.MSTATUS_MIE
        else:
            new &= ~isa.MSTATUS_MIE
        new |= isa.MSTATUS_MPIE                          # MPIE <- 1
        self.csr[isa.CSR["mstatus"]] = new % MOD
        target = s(self.csr[isa.CSR["mepc"]])
        if target % 4 == 0 and 0 <= target // 4 < len(self.imem):
            self.pc = target                    # mepc 是 tryte 地址，//4 得指令槽
        else:
            self.halted = True

    def _csr_op(self, name, rd, rs1, csr_addr):
        """CSRRW/RS/RC 及立即数版；返回写入 rd 的旧值。"""
        if rs1 is None:
            rs1 = 0
        old = self._csr_read(csr_addr) % MOD
        # 立即数版：rs1 字段即 uimm（3 trit，0..26）；否则取寄存器值
        src = rs1 if name.endswith("I") else (self.regs[rs1] % MOD if rs1 < isa.NREG else 0)
        if name in ("CSRRW", "CSRRWI"):
            self._csr_write(csr_addr, src)
        elif name in ("CSRRS", "CSRRSI"):
            self._csr_write(csr_addr, old | src)
        elif name in ("CSRRC", "CSRRCI"):
            self._csr_write(csr_addr, old & ~src)
        self.regs[rd] = old % MOD

    def _syscall(self):
        """RISC-V 约定：a7(x17)=号，a0..a5(x10..x15)=参数，返回值写 a0。"""
        num = s(self.regs[17])
        a0, a1, a2 = s(self.regs[10]), s(self.regs[11]), s(self.regs[12])
        a3, a4, a5 = s(self.regs[13]), s(self.regs[14]), s(self.regs[15])
        # 兼容旧约定：a7=0 视为传统停机（无 syscall 号的老程序）
        if num == 0:
            self.halted = True
            return
        ret = 0
        if num == SYS_WRITE:
            # write(fd, buf, count) -> 写入 tryte 数；fd=1 stdout, fd=2 stderr
            if a2 < 0:
                ret = EINVAL                       # 畸形 count：与 C VM 统一返回 -EINVAL
            elif a0 in (1, 2) and 0 <= a1 < len(self.mem):
                data = bytes(self.mem[a1:a1 + a2])
                self.output.extend(data)
                ret = len(data)
            else:
                ret = EBADF
        elif num == SYS_READ:
            # read(fd, buf, count)：从 stdin 取（仅 fd=0）
            if a0 == 0 and 0 <= a1 < len(self.mem):
                n = min(a2, len(self.stdin), len(self.mem) - a1)
                chunk = bytes(self.stdin[:n]); del self.stdin[:n]
                self.mem[a1:a1 + n] = chunk
                ret = n
            else:
                ret = 0               # EOF / 非法 fd
        elif num == SYS_READ8:
            # read8(fd, buf, n_octets)：每 octet 编 2 tryte [lo=o%243, hi=o/243]
            if a0 == 0 and 0 <= a1 and a1 + 2 * a2 <= len(self.mem):
                n = min(a2, len(self.stdin))
                for i in range(n):
                    o = self.stdin[i]
                    self.mem[a1 + 2 * i] = o % 243
                    self.mem[a1 + 2 * i + 1] = o // 243
                del self.stdin[:n]
                ret = n
            else:
                ret = 0
        elif num == SYS_WRITE8:
            # write8(fd, buf, n_octets)：每 2 tryte [lo,hi] 解 1 octet
            if a0 in (1, 2) and 0 <= a1 and a1 + 2 * a2 <= len(self.mem):
                for i in range(a2):
                    lo = self.mem[a1 + 2 * i] % 243
                    hi = self.mem[a1 + 2 * i + 1] % 243
                    o = lo + 243 * hi
                    self.output.append(o & 0xFF)   # 视图契约：非法 hi 截断到 octet
                ret = a2
            else:
                ret = -9               # E_BADF
        elif num in (SYS_EXIT, SYS_EXIT_GROUP):
            # exit / exit_group：停机并记录退出码
            self.exit_code = a0 & 0xFF
            self.halted = True
            ret = a0
        elif num == SYS_BRK:
            # brk(0)=查询；brk(addr)=精确增长/收缩；越界返回旧堆顶。
            if a0 == 0:
                ret = self.brk_ptr
            elif self.heap_base <= a0 < self.mmap_ptr:
                self.brk_ptr = a0
                ret = a0
            else:
                ret = self.brk_ptr
        elif num == SYS_GETPID:
            ret = self.pid
        elif num == SYS_GETTID:
            ret = self.tid
        elif num == SYS_SET_TID_ADDR:
            # set_tid_address(ptr)：记录子进程退出时需清零的地址，返回 tid
            self.clear_tid_addr = a0
            ret = self.tid
        elif num == SYS_CLOCK_GETTIME:
            # clock_gettime(clk_id, timespec*)：写 sec(低字)+nsec(高字)，各 9 tryte
            self.clock_ns += 1000000          # 每次推进 1 ms（单调、确定性）
            if 0 <= a1 < len(self.mem) - 2 * self.WORD_TRYTES:
                self._store_word(a1, self.clock_ns // 1000000000)
                self._store_word(a1 + self.WORD_TRYTES, self.clock_ns % 1000000000)
                ret = 0
            else:
                ret = EINVAL
        elif num == SYS_GETRANDOM:
            # getrandom(buf, count, flags)：xorshift32 PRNG，确定性，写 count tryte
            if 0 <= a0 < len(self.mem):
                # getrandom(buf=a0, count=a1, flags=a2)：count 在 a1
                n = min(a1, len(self.mem) - a0)
                for i in range(n):
                    x = self.rng_state
                    x ^= (x << 13) & 0xFFFFFFFF
                    x ^= x >> 17
                    x ^= (x << 5) & 0xFFFFFFFF
                    self.rng_state = x & 0xFFFFFFFF
                    self.mem[a0 + i] = self.rng_state % 243
                ret = n
            else:
                ret = EINVAL
        elif num == SYS_MMAP:
            # mmap(addr, len, prot, flags, fd, offset)：匿名映射，高位向下分配，9 对齐
            length = ((a1 + self.WORD_TRYTES - 1) // self.WORD_TRYTES) * self.WORD_TRYTES
            addr = self.mmap_ptr - length
            if length > 0 and self.brk_ptr < addr:
                self.mmap_ptr = addr
                ret = addr
            else:
                ret = ENOMEM
        elif num == SYS_MUNMAP:
            ret = 0                   # 简化：不回收，单调 mmap 区（够 glibc 启动）
        elif num == SYS_MPROTECT:
            ret = 0                   # 简化：VM 无页保护
        elif num == SYS_FUTEX:
            # futex：单线程 VM 无需阻塞，直接返回 0（唤醒 0 个等待者）
            ret = 0
        elif num == SYS_SCHED_YIELD:
            ret = 0
        elif num == SYS_OPENAT:
            # openat：VM 无真实文件系统，返回合法 fd 并记录（供 close/fstat 用）
            fd = self.next_fd; self.next_fd += 1
            self.open_files[fd] = a1  # 记录 filename 地址
            ret = fd
        elif num == SYS_CLOSE:
            ret = 0 if self.open_files.pop(a0, None) is not None else EBADF
        elif num in (SYS_FSTAT, SYS_FSTATAT, SYS_LSEEK):
            ret = 0                   # 简化：状态查询成功，大小/偏移 0
        elif num == SYS_UNAME:
            # uname(buf)：写机器名 "TriRV"（tryte 值），返回 0
            if 0 <= a0 < len(self.mem):
                for i, ch in enumerate(b"TriRV"):
                    self.mem[a0 + i] = ch % 243
                ret = 0
            else:
                ret = EINVAL
        elif num in (SYS_RT_SIGACTION, SYS_RT_SIGPROCMASK, SYS_RT_SIGRETURN,
                     SYS_GETCWD, SYS_READV, SYS_WRITEV):
            ret = 0                   # 信号/杂项：单线程 VM 接受但不产生异步行为
        else:
            ret = ENOSYS
        self.syscalls.append((num, a0, a1, a2))
        self.regs[10] = u(ret)        # 返回值写回 a0

    def step(self):
        # PC 是 tryte 地址：指令 i 位于地址 4*i（20 trit = 4 tryte）。
        # 非法 PC（负、非 4 对齐、越界）统一停机，避免负索引回绕或错位取指。
        if self.halted or self.pc < 0 or self.pc % 4 != 0 or self.pc >= 4 * len(self.imem):
            self.halted = True
            return
        inst = self.imem[self.pc // 4]
        old_pc = self.pc
        self.pc += 4                            # PC 前进（TC/全宽统一——TC 分支内跳转用 old_pc）
        # ---- TC 压缩格式识别（T10c-1）：opcode 23 保留 → slot 内嵌 C 语义 ----
        if (inst % 27) == isa.TC_OPCODE:
            ctry = [(inst // (243 ** i)) % 243 for i in range(3)]
            cd = isa.decode_c(ctry)
            self._exec_c(cd, old_pc)
            self.steps += 1
            return
        d = isa.decode(inst)
        name = d["inst"]
        rd, rs1, rs2 = d["rd"], d["rs1"], d["rs2"]
        imm = d.get("imm", 0)

        if name is None:
            self._take_trap(isa.CAUSE_ILLEGAL, inst, old_pc)
            self.steps += 1
            return

        # rs1/rs2 字段在 I/U/J/S/B 格式里可能存的是立即数片段（>=32），
        # 不是合法寄存器索引。钳制到 0，避免越界；真正的操作数在各分支内按需读取。
        rs1 = rs1 if rs1 < isa.NREG else 0
        rs2 = rs2 if rs2 < isa.NREG else 0
        a = s(self.regs[rs1])
        b = s(self.regs[rs2])

        if name == "ADD":
            self.regs[rd] = u(a + b)
        elif name == "SUB":
            self.regs[rd] = u(a - b)
        elif name == "MUL":
            self.regs[rd] = u(a * b)
        elif name in ("MULH", "MULHSU", "MULHU"):
            # 90-trit 全乘积的高半（Python 大整数天然无溢出）。
            # 有符号操作数用 s() 解释；无符号操作数直接用 word (0..MOD-1)。
            av, bv = self.regs[rs1], self.regs[rs2]
            if name == "MULHU":
                prod = av * bv                      # 无 × 无
            elif name == "MULHSU":
                prod = s(av) * bv                   # 有 × 无
            else:
                prod = s(av) * s(bv)                # 有 × 有
            hi = prod // MOD                        # 向 3^45 取整的高半
            self.regs[rd] = u(hi)                   # 以 3 补码存回 word
        elif name == "DIV":
            if b == 0:
                self.regs[rd] = u(0)
            else:
                q = abs(a) // abs(b)
                self.regs[rd] = u(q if (a >= 0) == (b >= 0) else -q)  # 向零取整
        elif name == "REM":
            if b == 0:
                self.regs[rd] = u(0)
            else:
                q = abs(a) // abs(b)
                q = q if (a >= 0) == (b >= 0) else -q
                self.regs[rd] = u(a - q * b)
        elif name == "DIVU":
            av, bv = self.regs[rs1], self.regs[rs2]  # 无符号 word
            self.regs[rd] = (av // bv) % MOD if bv else u(0)
        elif name == "REMU":
            av, bv = self.regs[rs1], self.regs[rs2]
            self.regs[rd] = (av % bv) % MOD if bv else u(0)
        elif name == "ADDI":
            self.regs[rd] = u(a + s(isa.sign_extend_imm(imm)))
        elif name == "SLL":
            # 三进制左移 n 位 = 乘 3^n（移位量取 rs2 低 7 trit）
            sh = self.regs[rs2] % (3**7)
            self.regs[rd] = (self.regs[rs1] * pow(3, sh, MOD)) % MOD
        elif name == "SRL":
            sh = self.regs[rs2] % (3**7)
            self.regs[rd] = (self.regs[rs1] // pow(3, sh)) % MOD
        elif name == "SRA":
            # 算术右移：有符号值 / 3^sh，向零取整（与 DIV 及 C VM divmod_word 语义一致）。
            # sh >= XLEN 时商必为 0，提前收敛避免 3^sh 大数。
            sh = self.regs[rs2] % (3**7)
            av = s(self.regs[rs1])
            if sh >= XLEN:
                self.regs[rd] = 0
            else:
                q = abs(av) // (3 ** sh)
                self.regs[rd] = u(q if av >= 0 else -q)
        elif name == "SLT":
            self.regs[rd] = 1 if a < b else 0
        elif name == "SLTU":
            self.regs[rd] = 1 if self.regs[rs1] < self.regs[rs2] else 0
        elif name == "AND":
            self.regs[rd] = self._tritwise("min", self.regs[rs1], self.regs[rs2])
        elif name == "OR":
            self.regs[rd] = self._tritwise("max", self.regs[rs1], self.regs[rs2])
        elif name == "XOR":
            self.regs[rd] = self._tritwise("xor", self.regs[rs1], self.regs[rs2])
        elif name == "SLTI":
            self.regs[rd] = 1 if a < s(isa.sign_extend_imm(imm)) else 0
        elif name == "SLTIU":
            immu = isa.sign_extend_imm(imm) % MOD
            self.regs[rd] = 1 if self.regs[rs1] < immu else 0
        elif name == "XORI":
            self.regs[rd] = self._tritwise("xor", self.regs[rs1],
                                           isa.sign_extend_imm(imm) % MOD)
        elif name == "ORI":
            self.regs[rd] = self._tritwise("max", self.regs[rs1],
                                           isa.sign_extend_imm(imm) % MOD)
        elif name == "ANDI":
            self.regs[rd] = self._tritwise("min", self.regs[rs1],
                                           isa.sign_extend_imm(imm) % MOD)
        elif name == "SLLI":
            # 立即数左移：移位量取 imm 低 7 trit（无符号解释），乘 3^sh。
            sh = imm % (3**7)
            self.regs[rd] = (self.regs[rs1] * pow(3, sh, MOD)) % MOD
        elif name == "SRLI":
            sh = imm % (3**7)
            self.regs[rd] = (self.regs[rs1] // pow(3, sh)) % MOD
        elif name == "SRAI":
            sh = imm % (3**7)
            av = s(self.regs[rs1])
            if sh >= XLEN:
                self.regs[rd] = 0
            else:
                q = abs(av) // (3 ** sh)
                self.regs[rd] = u(q if av >= 0 else -q)
        elif name == "BEQ":
            if s(self.regs[rs1]) == s(self.regs[rs2]):
                self.pc = old_pc + 4 * s(isa.sign_extend_imm(imm))
        elif name == "BNE":
            if s(self.regs[rs1]) != s(self.regs[rs2]):
                self.pc = old_pc + 4 * s(isa.sign_extend_imm(imm))
        elif name == "BLT":
            if a < b:
                self.pc = old_pc + 4 * s(isa.sign_extend_imm(imm))
        elif name == "BGE":
            if a >= b:
                self.pc = old_pc + 4 * s(isa.sign_extend_imm(imm))
        elif name == "BLTU":
            if self.regs[rs1] < self.regs[rs2]:
                self.pc = old_pc + 4 * s(isa.sign_extend_imm(imm))
        elif name == "BGEU":
            if self.regs[rs1] >= self.regs[rs2]:
                self.pc = old_pc + 4 * s(isa.sign_extend_imm(imm))
        elif name == "SW":
            # 字存储：9 tryte（局部变量/栈/指针目标）
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            self._store_word(addr, self.regs[rs2])
            if self.lr_valid and self._overlap(addr, 9, self.lr_addr): self.lr_valid = False
        elif name == "SB":
            # tryte 存储：1 tryte（char/字符串缓冲区）
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            self._store_tryte(addr, self.regs[rs2])
            if self.lr_valid and self._overlap(addr, 1, self.lr_addr): self.lr_valid = False
        elif name == "SH":
            # 半字 = 2 tryte
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            self._store_tryte(addr, self.regs[rs2])
            self._store_tryte(addr + 1, self.regs[rs2] // 243)
            if self.lr_valid and self._overlap(addr, 2, self.lr_addr): self.lr_valid = False
        elif name == "SW4":
            # 4-tryte 存储（__tint2 档）：低 20-trit 截断（mod 3^20）
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            w = self.regs[rs2]
            for k in range(4):
                self._store_tryte(addr + k, w // 243 ** k)
            if self.lr_valid and self._overlap(addr, 4, self.lr_addr): self.lr_valid = False
        elif name == "LW":
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            self.regs[rd] = self._load_word(addr)
        elif name in ("LB","LBU","LH","LHU"):
            # 与 RISC-V 完全一致：LB/LH 符号扩展，LBU/LHU 零扩展。
            # 5-trit tryte 的有符号范围是 -121..121（半阈值 (3^5-1)//2 = 121）；
            # 10-trit 半字是 -29524..29524（(3^10-1)//2 = 29524）。
            # char 本身无符号（RISC-V psABI 亦如此），故编译器对 char 用 LBU，
            # 只有 signed char 才用 LB。
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            if name in ("LB","LBU"):
                t = self._load_tryte(addr)         # 0..242，1 tryte = 5 trit
                if name == "LBU":
                    self.regs[rd] = u(t)
                else:
                    self.regs[rd] = u(t - 243 if t > 121 else t)
            else:  # LH / LHU = 2 tryte = 10 trit
                lo = self._load_tryte(addr)
                hi = self._load_tryte(addr + 1)
                t = lo + hi * 243                   # 0..59048
                if name == "LHU":
                    self.regs[rd] = u(t)
                else:
                    self.regs[rd] = u(t - 59049 if t > 29524 else t)
        elif name == "LW4":
            # 4-tryte = 20-trit 装载（__tint2 档）：半阈值 (3^20-1)//2 符号扩展
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            t = 0
            for k in range(3, -1, -1):
                t = t * 243 + self._load_tryte(addr + k)
            self.regs[rd] = u(t - 3 ** 20 if t > (3 ** 20 - 1) // 2 else t)
        elif name == "TMAX":
            self.regs[rd] = self._tritwise("max", self.regs[rs1], self.regs[rs2])
        elif name == "TMIN":
            self.regs[rd] = self._tritwise("min", self.regs[rs1], self.regs[rs2])
        elif name == "TNOT":
            self.regs[rd] = self._tritwise("not", self.regs[rs1], 0)
        elif name == "LIT0":
            # Post literal x^0：逐 trit 2−t（Kleene ¬，词级 ≡ TNOT；§6.1）
            self.regs[rd] = self._tritwise("not", self.regs[rs1], 0)
        elif name in ("LIT1", "LIT2"):
            # Post literal x^a 循环：逐 trit (t+a) mod 3
            self.regs[rd] = self._tritwise("lit1" if name == "LIT1" else "lit2",
                                           self.regs[rs1], 0)
        elif name == "TAND":
            self.regs[rd] = self._tritwise("min", self.regs[rs1], self.regs[rs2])
        elif name == "TOR":
            self.regs[rd] = self._tritwise("max", self.regs[rs1], self.regs[rs2])
        elif name == "TNEG":
            self.regs[rd] = u(-s(self.regs[rs1]))       # 3 补码取负
        elif name == "TSEXT":
            # 取 rs1 低 5 trit（1 tryte）按 3 补码符号扩展到 45 trit。
            # 低 5 trit 无符号值 t∈[0,242]，半阈值 (3^5-1)//2=121；t>121 视为负 (t-243)。
            t5 = self.regs[rs1] % (3 ** 5)
            sv = t5 if t5 <= 121 else t5 - 243
            self.regs[rd] = u(sv)
        elif name == "TPACK":
            # 低 5 trit -> 1 byte (0..255)，与方案 C 打包一致
            t = [ (self.regs[rs1] // 3**i) % 3 for i in range(5) ]
            bnum = sum(t[i] * 3**i for i in range(5))   # 0..242
            self.regs[rd] = (((bnum * 256) + 242) // 243) % 256
        elif name == "TUNPACK":
            # byte -> 低 5 trit；解包从高位出，需按 d4..d0 反向加权
            byte = self.regs[rs1] % 256
            b, res = byte, 0
            digs = []
            for i in range(5):
                b = b * 3
                digs.append(b >> 8)     # 依次为 d4,d3,d2,d1,d0 (高位在前)
                b &= 0xFF
            for j, d in enumerate(digs):
                res += d * 3**(4 - j)   # d4->3^4, d0->3^0
            self.regs[rd] = res
        elif name in ("LRW", "SCW", "AMOADD", "AMOSWAP", "AMOAND", "AMOOR", "AMOXOR", "AMOMIN", "AMOMAX"):
            # A 扩展：原子内存操作。rs1=地址寄存器，rs2=源值（LR 忽略），rd=返回值。
            addr = s(self.regs[rs1])
            if name == "LRW":
                self.regs[rd] = self._load_word(addr)
                self.lr_addr, self.lr_valid = addr, True
            elif name == "SCW":
                if self.lr_valid and self.lr_addr == addr:
                    self._store_word(addr, self.regs[rs2])
                    self.regs[rd] = 0
                else:
                    self.regs[rd] = 1
                self.lr_valid = False              # SC 无论成败都清除保留
            else:
                old = self._load_word(addr)
                src = self.regs[rs2]
                if name == "AMOADD":  new = u(s(old) + s(src))
                elif name == "AMOSWAP": new = src
                elif name == "AMOAND":  new = self._tritwise("min", old, src)
                elif name == "AMOOR":   new = self._tritwise("max", old, src)
                elif name == "AMOXOR":  new = self._tritwise("xor", old, src)
                elif name == "AMOMIN":  new = u(min(s(old), s(src)))
                else:                   new = u(max(s(old), s(src)))  # AMOMAX
                self._store_word(addr, new)
                self.regs[rd] = old                # 返回旧值
                self.lr_valid = False              # AMO 成功破坏其他核的保留(单核自清)
        elif name == "JALR":
            self.regs[rd] = old_pc + 4            # 返回地址 = 下一条 tryte 地址
            # 目标 = rs1(tryte 地址) + imm(tryte 偏移)，向下对齐到 4（对标 RISC-V 清低位）
            tgt = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            self.pc = (tgt // 4) * 4
        elif name == "LUI":
            self.regs[rd] = u(imm * (3 ** 7))   # imm13 左移 7 trit
        elif name == "AUIPC":
            # 高位立即数 + 当前 PC（PC 为 tryte 地址，加数同为 tryte 偏移）
            self.regs[rd] = u(old_pc + imm * (3 ** 7))
        elif name == "JAL":
            self.regs[rd] = old_pc + 4            # 返回地址 = 下一条 tryte 地址
            self.pc = old_pc + 4 * s(isa.sign_extend_imm(imm, 13))
        elif name == "ECALL":
            # M 态 ECALL 直连 syscall（向后兼容）；U 态 ECALL 陷入 trap
            if self.priv == 3:
                self._syscall()
            else:
                self._take_trap(isa.CAUSE_ECALL_U, 0, old_pc)
        elif name == "EBREAK":
            self._take_trap(3, 0, old_pc)      # 断点异常
        elif name in ("CSRRW", "CSRRS", "CSRRC", "CSRRWI", "CSRRSI", "CSRRCI"):
            self._csr_op(name, rd, rs1, imm)   # imm = CSR 地址
        elif name == "MRET":
            self._mret()
        elif name == "WFI":
            pass                               # 等待中断：VM 中为提示，中断在步末检查
        elif name == "FENCE":
            pass                               # 内存屏障：单线程 VM 无乱序，视为 NOP
        elif name in ("FLW", "FSW", "FLW.S", "FSW.S"):
            # F 边界转换：内存 45-trit raw ↔ freg。offset 是 tryte 地址偏移。
            # FLW/FSW 只原样搬运 raw（不解释格式），两档共用；档位由后续算术指令的
            # funct2 决定。.S 变体（funct 0/2）与 .D（funct 1/3）在此无语义差别。
            addr = s(self.regs[rs1]) + s(isa.sign_extend_imm(imm))
            if name.startswith("FLW"):
                self.freg[rd] = self._load_word(addr) % MOD
            else:
                self._store_word(addr, self.freg[rs2] % MOD)
                if self.lr_valid and self._overlap(addr, 9, self.lr_addr):
                    self.lr_valid = False
        elif name in ("FADD", "FSUB", "FMUL", "FDIV", "FSQRT",
                      "FADD.S", "FSUB.S", "FMUL.S", "FDIV.S", "FSQRT.S"):
            # 档位：带 .S 后缀或 funct2==0 → TF20，否则 TF45。
            fmt = tf.TF20 if (name.endswith(".S") or d.get("funct2", 1) == 0) else tf.TF45
            base = name.split(".")[0]
            av, bv = self.freg[rs1] % MOD, self.freg[rs2] % MOD
            if base == "FADD":
                r = tf.f_add(av, bv, fmt)
            elif base == "FSUB":
                r = tf.f_add(av, bv, fmt, subtract=True)
            elif base == "FMUL":
                r = tf.f_mul(av, bv, fmt)
            elif base == "FDIV":
                r = tf.f_div(av, bv, fmt)
            else:
                r = tf.f_sqrt(av, fmt)
            self.freg[rd] = r % MOD
        elif name in ("FEQ", "FLT", "FLE", "FEQ.S", "FLT.S", "FLE.S"):
            fmt = tf.TF45 if d.get("funct2", 1) == 1 else tf.TF20
            opn = {"FEQ": "eq", "FLT": "lt", "FLE": "le"}[name.split(".")[0]]
            self.regs[rd] = tf.f_cmp(self.freg[rs1] % MOD, self.freg[rs2] % MOD,
                                     fmt, opn)
        elif name in ("FCVT.W", "FCVT.W.T", "FCVT.F"):
            # 整数 ↔ 浮点（TF45）：FCVT.W rd=freg→整数写 rd（round-to-nearest）；
            # FCVT.W.T 向零截断（rtz，C 的 (int) cast / wasm trunc 语义）；
            # FCVT.F 整数 rs1→freg[rd]
            if name == "FCVT.W":
                self.regs[rd] = u(round(tf.TF45.decode(self.freg[rs1] % MOD)))
            elif name == "FCVT.W.T":
                self.regs[rd] = u(int(tf.TF45.decode(self.freg[rs1] % MOD)))  # int() = 向零截断
            else:
                self.freg[rd] = tf.TF45.encode(float(s(self.regs[rs1]))) % MOD
        else:
            raise RuntimeError(f"未实现指令: {name}")

        # 异步中断检查：MIE=1 且定时器挂起 -> 陷入（在指令提交后、下一条之前）
        if (not self.halted) and self.irq_timer and (self.csr[isa.CSR["mstatus"]] & isa.MSTATUS_MIE):
            self.irq_timer = False
            self._take_trap(isa.CAUSE_MTIMER, 0, self.pc)
        # x0 硬连零：任何指令尝试写 x0 都不生效（RISC-V 语义）
        self.regs[0] = 0
        self.steps += 1

    def _tritwise(self, op, x, y):
        """逐 trit 三值运算。三进制寄存器里**没有二进制位**，所以 AND/OR/XOR
        只能是逐 trit 的：AND=min、OR=max、XOR=(a+b) mod 3。
        min/max 与 TAND/TOR 同义（RISC-V 兼容名 + 三值逻辑名）。"""
        res = 0
        p = 1
        for _ in range(XLEN):
            x, dx = divmod(x, 3)
            y, dy = divmod(y, 3)
            if op == "max":
                d = dx if dx > dy else dy
            elif op == "min":
                d = dx if dx < dy else dy
            elif op == "not":
                d = 2 - dx
            elif op == "lit1":
                d = (dx + 1) % 3
            elif op == "lit2":
                d = (dx + 2) % 3
            else:                                   # xor：逐 trit 模 3 相加
                d = (dx + dy) % 3
            res += d * p
            p *= 3
        return res

    def run(self, max_steps=10000, trace=False):
        while not self.halted and self.steps < max_steps:
            if trace:
                pc = self.pc
                idx = pc // 4 if pc % 4 == 0 else -1
                d = isa.decode(self.imem[idx]) if 0 <= idx < len(self.imem) else {}
                print(f"  pc={pc:3d}/{idx:2d} {d.get('inst','?'):8s} " +
                      " ".join(f"x{i}={s(self.regs[i])}" for i in range(1, 8)))
            self.step()
        return self


def prog():
    """程序：计算 1+2+3+4+5，存 x10；再算 TMAX(x10, 20) 存 x11；ECALL 退出。"""
    e = isa.encode
    p = []
    p.append(e("ADDI", rd=1, rs1=0, imm=0))    # x1 = 0  累加器
    p.append(e("ADDI", rd=2, rs1=0, imm=1))    # x2 = 1  步长
    p.append(e("ADDI", rd=3, rs1=0, imm=5))    # x3 = 5  计数
    # 循环: x1 += x2; x3 -= 1; if x3 != 0 goto loop(-2)
    p.append(e("ADD",  rd=1, rs1=1, rs2=2))    # x1 += x2
    p.append(e("ADDI", rd=3, rs1=3, imm=3**7-1))  # x3 -= 1 (imm=-1)
    p.append(e("BNE",  rs1=3, rs2=0, imm=3**7-2)) # if x3!=0 goto pc-2
    p.append(e("ADDI", rd=10, rs1=1, imm=0))   # x10 = x1 (结果 5)
    p.append(e("ADDI", rd=4, rs1=0, imm=20))   # x4 = 20
    p.append(e("TMAX", rd=11, rs1=10, rs2=4))  # x11 = TMAX(x10, x4)
    p.append(e("ECALL"))
    return p


if __name__ == "__main__":
    print("=== TriRV VM 求和程序 ===")
    vm = VM(prog()).run(trace=True)
    print("=== 结果 ===")
    print(f"  累加和 x10 = {s(vm.regs[10])}  (期望 5)")
    print(f"  TMAX x11  = {vm.tritwise_str(vm.regs[11]) if hasattr(vm,'tritwise_str') else isa.trit_str(vm.regs[11],45)}")
    print(f"  执行步数 = {vm.steps}")
    assert s(vm.regs[10]) == 5, "累加结果错误"
    print("  ✓ 断言通过")
