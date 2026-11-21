/* strsignal —— musl src/string/strsignal.c 的三进制原生改编。
 *
 * 与上游差异：
 *   1. 上游用 C99 指定初始化器 `[SIGHUP]=1` 构建信号号→字符串表；
 *      TriRV C 子集的全局数组初始化列表/指定初始化器未支持；
 *      switch 大 case 数也会触发表达式嵌套深度限制 → 用顺序
 *      if-else 链（每层 2 temps，深度无限制），语义等价
 *   2. 信号号常量内联（上游来自 <signal.h>；TriRV 无信号子系统，
 *      这里只是字符串表索引语义，不涉及真实信号机制）
 *   3. 返回 char *（上游 const char *）；未知名返回 "Unknown signal"
 * 覆盖 POSIX 1-31 标准信号（与 musl strsignal 的表范围一致）。
 */
char *strsignal(int sig)
{
	if (sig == 1) return "SIGHUP";
	else if (sig == 2) return "SIGINT";
	else if (sig == 3) return "SIGQUIT";
	else if (sig == 4) return "SIGILL";
	else if (sig == 5) return "SIGTRAP";
	else if (sig == 6) return "SIGABRT";
	else if (sig == 7) return "SIGBUS";
	else if (sig == 8) return "SIGFPE";
	else if (sig == 9) return "SIGKILL";
	else if (sig == 10) return "SIGUSR1";
	else if (sig == 11) return "SIGSEGV";
	else if (sig == 12) return "SIGUSR2";
	else if (sig == 13) return "SIGPIPE";
	else if (sig == 14) return "SIGALRM";
	else if (sig == 15) return "SIGTERM";
	else if (sig == 16) return "SIGSTKFLT";
	else if (sig == 17) return "SIGCHLD";
	else if (sig == 18) return "SIGCONT";
	else if (sig == 19) return "SIGSTOP";
	else if (sig == 20) return "SIGTSTP";
	else if (sig == 21) return "SIGTTIN";
	else if (sig == 22) return "SIGTTOU";
	else if (sig == 23) return "SIGURG";
	else if (sig == 24) return "SIGXCPU";
	else if (sig == 25) return "SIGXFSZ";
	else if (sig == 26) return "SIGVTALRM";
	else if (sig == 27) return "SIGPROF";
	else if (sig == 28) return "SIGWINCH";
	else if (sig == 29) return "SIGIO";
	else if (sig == 30) return "SIGPWR";
	else if (sig == 31) return "SIGSYS";
	return "Unknown signal";
}
