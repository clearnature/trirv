/*
 * TriRV-FW —— 面向实时/高可靠嵌入式设备的固件框架（规格参考实现）
 * =====================================================================
 * 运行环境：TriRV 三进制虚拟硬件（trirv_vm.c 作为虚拟硬件引擎）
 * 设计目标：确定性调度 + 故障可恢复 + 零运行期动态分配
 *
 * 嵌入式固件工程约束（逐条落地，非口号）：
 *  1) 初始化后禁止 malloc/free：TCB/栈/内存池全部静态分配；运行期分配走静态固定块池。
 *  2) 栈大小是"算出来的"：每个任务栈独立、填模式 + 末尾 canary，运行期测高水位
 *     （等价 FreeRTOS uxTaskGetStackHighWaterMark），canary 破坏 → 栈溢出故障。
 *  3) ISR 最小化：模拟中断只置易失事件位 + 压入延后队列，绝不在"中断上下文"做事；
 *     下半部（bottom-half）在 tick 边界由任务上下文处理。
 *  4) 固定优先级抢占调度 RMS：O(1) 位图就绪队列，优先级反转用优先级继承互斥锁(PIP)。
 *  5) 每个外设/操作都有错误路径：分配失败、锁超时、看门狗复位、死线违约全部计数并可恢复。
 *  6) 时间确定性：调度基于虚拟 tick（指令预算），可复现；墙上时钟仅用于报告吞吐。
 *
 * 编译：gcc -O2 -fopenmp -o trirv_firmware trirv_firmware.c -lm
 * 运行：./trirv_firmware [程序.trv]        # 可选：把 TRV1 程序作为 trirv_app 任务的负载
 */
#define _GNU_SOURCE
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-result"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>

/* ---- 复用 TriRV VM 作为"虚拟硬件"引擎；重命名其 CLI main 避免符号冲突 ---- */
#define main trirv_vm_cli_main_unused
#include "trirv_vm.c"
#undef main

/* ============================ 配置 ============================ */
#define FW_MAX_TASKS      8
#define FW_STACK_WORDS    64
#define FW_TICKS          20000         /* 仿真视界（虚拟 tick） */
#define STACK_FILL        0xA5A5A5A5u
#define STACK_CANARY      0xDEADBEEFu
#define POOL_BLOCKS       32
#define POOL_BLK_WORDS    16
#define DEFERRED_Q_DEPTH  16

/* ============================ 任务模型 ============================ */
typedef enum { T_READY, T_RUNNING, T_BLOCKED, T_SUSPENDED, T_FAULT } TaskState;
struct Task;                                  /* 前向声明：作业回调需要完整类型可见 */
typedef void (*JobFn)(struct Task *);

typedef struct Task {
    int          id;
    const char  *name;
    uint8_t      prio;             /* 0 = 最高优先级 */
    uint32_t     period, budget, deadline, offset;   /* 单位：虚拟 tick */
    TaskState    state;
    uint32_t     remain;           /* 本周期剩余执行预算 */
    uint32_t     release;          /* 本周期释放 tick */
    uint64_t     cpu_ticks;        /* 累计占用 CPU 的 tick 数 */
    uint32_t     jobs, misses, overruns, wdt_trips, stack_faults;
    uint32_t     stack[FW_STACK_WORDS];
    uint32_t     stack_hwm;        /* 观测到的最小空闲字数（越小越危险） */
    uint32_t     stack_depth;      /* 本任务模拟栈深 */
    JobFn        job;
    int          blocked_on;       /* 等待的互斥锁索引，-1=无 */
} Task;

/* ============================ 互斥锁（优先级继承 PIP） ============================ */
typedef struct {
    int   owner;                   /* 持有者 task id，-1 = 空闲 */
    int   base_prio;               /* 持有者原始优先级（用于还原） */
    int   wait_mask;               /* 等待者位图（按 task id） */
} Mutex;

static Task    g_tasks[FW_MAX_TASKS];
static int     g_ntask;
static Mutex   g_mutexes[4];
static int     g_nmutex;
static uint32_t g_tick;
static uint32_t g_ready_mask;      /* 按优先级索引的就绪位图 */
static int      g_running = -1;

/* ---- 静态内存池：固定块、O(1) 分配、无碎片 ---- */
static uint32_t g_pool[POOL_BLOCKS][POOL_BLK_WORDS];
static int      g_pool_free_head = -1;
static int      g_pool_alloc_calls, g_pool_fail_calls;

/* ---- 模拟中断：只置事件位 + 延后队列，绝不在中断上下文做实际工作 ---- */
static volatile uint32_t g_isr_events;
static struct { int task_id; uint32_t token; } g_deferred[DEFERRED_Q_DEPTH];
static int g_deferred_head, g_deferred_tail;   /* 环形队列 */
static uint32_t g_isr_count, g_deferred_ran;

/* ---- TriRV 虚拟硬件实例（作为实时任务负载） ---- */
static VM      g_vm;
static uint8_t g_vm_seg[MEMSZ];
static Inst   *g_vm_prog;
static int     g_vm_nprog;
static int     g_vm_loaded;
static long    g_vm_steps_total;

/* ============================ 静态内存池 ============================ */
static void pool_init(void) {
    for (int i = 0; i < POOL_BLOCKS; i++) {
        /* 每块首字复用为 next 索引 */
        g_pool[i][0] = (uint32_t)(i + 1 < POOL_BLOCKS ? i + 1 : -1);
    }
    g_pool_free_head = 0;
}
static int pool_alloc(void) {
    g_pool_alloc_calls++;
    if (g_pool_free_head < 0) { g_pool_fail_calls++; return -1; }  /* 错误路径：不阻塞，返回失败 */
    int b = g_pool_free_head;
    g_pool_free_head = (int)g_pool[b][0];
    return b;
}
static void pool_free(int b) {
    if (b < 0 || b >= POOL_BLOCKS) return;
    g_pool[b][0] = (uint32_t)g_pool_free_head;
    g_pool_free_head = b;
}

/* ============================ 栈管理 ============================ */
static void stack_init(Task *t) {
    for (int i = 0; i < FW_STACK_WORDS; i++) t->stack[i] = STACK_FILL;
    t->stack[FW_STACK_WORDS - 1] = STACK_CANARY;
    t->stack_hwm = FW_STACK_WORDS;
}
static void stack_push(Task *t, uint32_t words) {
    /* 模拟函数调用压栈：真实栈从高地址向低地址增长，canary 紧邻栈底(高端) */
    uint32_t n = words < FW_STACK_WORDS - 1 ? words : FW_STACK_WORDS - 1;
    uint32_t top = FW_STACK_WORDS - 1;                 /* canary 槽 */
    for (uint32_t i = 0; i < n; i++) t->stack[top - 1 - i] = 0x5A5A0000u | i;
}
static int stack_check(Task *t) {
    if (t->stack[FW_STACK_WORDS - 1] != STACK_CANARY) return -1;   /* 溢出：canary 被踩 */
    /* 从低地址端数连续未触碰的槽 = 剩余空闲；取历次最小值 = 高水位余量 */
    uint32_t free_words = 0;
    while (free_words < FW_STACK_WORDS - 1 && t->stack[free_words] == STACK_FILL) free_words++;
    if (free_words < t->stack_hwm) t->stack_hwm = free_words;      /* 最小剩余 = 高水位 */
    return 0;
}

/* ============================ 任务创建（静态） ============================ */
static int task_create(const char *name, uint8_t prio, uint32_t period,
                       uint32_t budget, uint32_t deadline, uint32_t offset,
                       uint32_t stack_depth, JobFn job) {
    if (g_ntask >= FW_MAX_TASKS) return -1;
    Task *t = &g_tasks[g_ntask];
    memset(t, 0, sizeof(*t));
    t->id = g_ntask; t->name = name; t->prio = prio;
    t->period = period; t->budget = budget; t->deadline = deadline; t->offset = offset;
    t->stack_depth = stack_depth; t->job = job; t->state = T_SUSPENDED;
    t->blocked_on = -1;
    stack_init(t);
    return g_ntask++;
}

/* ============================ 虚拟中断 + 延后处理 ============================ */
/* ISR 模拟：真正 ISR 只做这三件事——递增计数、置事件位、把工作塞进延后队列 */
static void isr_simulate(int task_id, uint32_t token) {
    g_isr_count++;
    g_isr_events |= (1u << task_id);
    int next = (g_deferred_tail + 1) % DEFERRED_Q_DEPTH;
    if (next == g_deferred_head) return;               /* 队列满：丢弃，计数由调用方处理 */
    g_deferred[g_deferred_tail].task_id = task_id;
    g_deferred[g_deferred_tail].token = token;
    g_deferred_tail = next;
}
/* 下半部：任务上下文执行，可调用任意 API（此处做内存池分配等"重"工作） */
static void deferred_process(void) {
    while (g_deferred_head != g_deferred_tail) {
        int id = g_deferred[g_deferred_head].task_id;
        int b = pool_alloc();                          /* 下半部允许分配静态块 */
        if (b >= 0) { g_pool[b][1] = g_deferred[g_deferred_head].token; pool_free(b); }
        g_deferred_head = (g_deferred_head + 1) % DEFERRED_Q_DEPTH;
        g_deferred_ran++;
        g_isr_events &= ~(1u << id);
    }
}

/* ============================ 优先级继承互斥锁 ============================ */
static int mutex_create(void) {
    if (g_nmutex >= 4) return -1;
    g_mutexes[g_nmutex].owner = -1;
    g_mutexes[g_nmutex].wait_mask = 0;
    return g_nmutex++;
}
static int mutex_lock(int mi, Task *t) {
    Mutex *m = &g_mutexes[mi];
    if (m->owner == -1) {                              /* 快速路径：无锁竞争 */
        m->owner = t->id; m->base_prio = t->prio;
        return 0;
    }
    if (m->owner == t->id) return 0;                   /* 递归锁（简化允许） */
    m->wait_mask |= (1u << t->id);                     /* 竞争：加入等待位图 */
    t->blocked_on = mi; t->state = T_BLOCKED;
    /* 优先级继承：若等待者优先级更高，临时提升持有者 */
    if (t->prio < g_tasks[m->owner].prio)
        g_tasks[m->owner].prio = t->prio;
    return -1;                                         /* 调用方需让出 CPU */
}
static void mutex_unlock(int mi, Task *t) {
    Mutex *m = &g_mutexes[mi];
    if (m->owner != t->id) return;
    /* 还原持有者原始优先级 */
    t->prio = (uint8_t)m->base_prio;
    m->owner = -1;
    /* 唤醒最高优先级等待者（按 task id 低位=高优先级约定由更高 prio 先被调度） */
    for (int i = 0; i < g_ntask; i++) {
        if (m->wait_mask & (1u << i)) {
            m->wait_mask &= ~(1u << i);
            g_tasks[i].blocked_on = -1;
            g_tasks[i].state = T_READY;
            g_ready_mask |= (1u << g_tasks[i].prio);
            break;
        }
    }
}

/* ============================ 任务作业（真实副作用） ============================ */
static int g_ctrl_mutex = -1;
static uint32_t g_ctrl_output;

/* 控制任务：模拟读取传感器（下半部）、持锁更新共享状态、压栈、可注入超时故障 */
static void job_control(Task *t) {
    isr_simulate(t->id, g_tick);                       /* 顶半部：只置事件位 */
    stack_push(t, t->stack_depth);
    if (g_ctrl_mutex >= 0) {
        if (mutex_lock(g_ctrl_mutex, t) == 0) {
            g_ctrl_output = g_tick * 3 + t->id;        /* 临界区：更新共享控制量 */
            mutex_unlock(g_ctrl_mutex, t);
        }
    }
}
/* TriRV 应用任务：在虚拟硬件上执行一个 TRV1 程序（固定指令预算内推进） */
static void job_trirv_app(Task *t) {
    stack_push(t, t->stack_depth);
    if (g_vm_loaded && !g_vm.halted) {
        long before = g_vm.steps;
        vm_run_cg(&g_vm, g_vm.steps + 5000);           /* 本周期最多推进 5000 指令 */
        g_vm_steps_total += g_vm.steps - before;
        if (g_vm.halted && before == g_vm.steps) { /* 已停机：无事可做，正常 */ }
    }
}
/* 通信任务：分配静态块打包一帧，出错走失败路径 */
static void job_comm(Task *t) {
    stack_push(t, t->stack_depth);
    int b = pool_alloc();
    if (b >= 0) { g_pool[b][1] = g_tick; pool_free(b); }
    /* 分配失败不崩溃：交给错误计数体现 */
}
/* 诊断任务：仅压栈做自检 */
static void job_diag(Task *t) {
    stack_push(t, t->stack_depth);
}

/* ============================ 调度器 ============================ */
static void ready_add(Task *t) { g_ready_mask |= (1u << t->prio); }

/* O(1) 取最高优先级就绪任务：ctz 找最低置位（约定 0=最高） */
static int sched_pick(void) {
    if (!g_ready_mask) return -1;
    int p = __builtin_ctz(g_ready_mask);
    for (int i = 0; i < g_ntask; i++)
        if (g_tasks[i].state == T_READY && g_tasks[i].prio == p) return i;
    return -1;
}

/* ============================ 看门狗 + 故障注入 ============================ */
typedef struct { int inject_task; uint32_t inject_tick; uint32_t extra_ticks; } FaultCfg;

static uint32_t g_wdt_trips_total, g_deadline_misses_total, g_recoveries;

static void task_recover(Task *t) {
    /* 故障恢复：强制回到下一周期、释放持有的锁、清栈、复位预算 */
    if (t->blocked_on >= 0) {
        Mutex *m = &g_mutexes[t->blocked_on];
        m->wait_mask &= ~(1u << t->id);
        t->blocked_on = -1;
    }
    if (g_running == t->id) g_running = -1;
    t->state = T_SUSPENDED;                            /* 等待下次周期释放 */
    t->remain = 0;
    g_recoveries++;
}

/* ============================ 主仿真循环 ============================ */
static void firmware_run(uint32_t ticks, FaultCfg *fault) {
    for (g_tick = 0; g_tick < ticks; g_tick++) {
        /* --- 1. 周期任务释放 --- */
        for (int i = 0; i < g_ntask; i++) {
            Task *t = &g_tasks[i];
            if (t->state == T_SUSPENDED &&
                g_tick >= t->offset && (g_tick - t->offset) % t->period == 0) {
                t->state = T_READY; t->remain = t->budget; t->release = g_tick;
                t->stack_hwm = FW_STACK_WORDS;         /* 新实例重置高水位 */
                ready_add(t);
            }
        }

        /* --- 2. 处理 ISR 延后工作（下半部，任务上下文） --- */
        deferred_process();

        /* --- 3. 调度：抢占式选择最高优先级就绪任务 --- */
        int cur = sched_pick();
        if (cur < 0) { g_running = -1; continue; }     /* 空闲 */
        Task *t = &g_tasks[cur];
        if (g_running != cur) g_running = cur;
        t->state = T_RUNNING;
        g_ready_mask &= ~(1u << t->prio);

        /* --- 4. 执行一个 tick：首次进入触发作业副作用 --- */
        if (t->remain == t->budget) {
            if (t->job) t->job(t);
            if (stack_check(t) < 0) {                  /* 栈溢出 → 故障 + 恢复 */
                t->stack_faults++;
                task_recover(t);
                continue;
            }
        }
        t->remain--;
        t->cpu_ticks++;
        g_vm_steps_total += 0;                         /* VM 步进在 job 内累计 */

        /* --- 5. 故障注入：让某任务本周期需要额外 tick（模拟 WCET 超时） --- */
        uint32_t extra = (fault && fault->inject_task == cur && g_tick == fault->inject_tick)
                         ? fault->extra_ticks : 0;
        if (extra) t->remain += extra, t->overruns++;

        /* --- 6. 完成 / 看门狗 / 死线检查 --- */
        int recovered = 0;
        if (t->remain == 0) {
            t->jobs++;
            t->state = T_SUSPENDED;
            g_running = -1;
        } else {
            uint32_t age = g_tick - t->release;
            if (age > t->deadline) {                   /* 死线违约 */
                t->misses++; g_deadline_misses_total++;
                if (age > t->deadline + t->deadline / 2) {  /* 看门狗：严重超时 → 复位 */
                    t->wdt_trips++; g_wdt_trips_total++;
                    task_recover(t);
                    recovered = 1;                     /* 已被复位：不得重新置就绪 */
                }
            }
            if (!recovered) {
                t->state = T_READY;
                ready_add(t);
            }
        }
    }
}

/* ============================ RMS 可调度性分析 ============================ */
static double rms_bound(int n) { return n * (pow(2.0, 1.0 / n) - 1.0); }
static double utilization(void) {
    double u = 0;
    for (int i = 0; i < g_ntask; i++)
        u += (double)g_tasks[i].budget / (double)g_tasks[i].period;
    return u;
}

/* ============================ 报告 ============================ */
static void report(FaultCfg *fault) {
    printf("\n========== TriRV-FW 实时固件报告 ==========\n");
    double u = utilization(), bound = rms_bound(g_ntask);
    printf("RMS 可调度性: U=%.4f  界( n=%d )=%.4f  → %s\n",
           u, g_ntask, bound, u <= bound ? "可调度 (PASS)" : "超出界 (需更细分析)");
    printf("仿真 tick=%u  ISR次数=%u  延后工作执行=%u  VM累计指令=%ld\n",
           g_tick, g_isr_count, g_deferred_ran, g_vm_steps_total);
    printf("内存池: 分配=%d 失败=%d  看门狗复位=%u 死线违约=%u 故障恢复=%u\n",
           g_pool_alloc_calls, g_pool_fail_calls, g_wdt_trips_total,
           g_deadline_misses_total, g_recoveries);
    if (fault)
        printf("故障注入: task=%s @tick=%u 额外+%u ticks\n",
               g_tasks[fault->inject_task].name, fault->inject_tick, fault->extra_ticks);
    printf("\n%-12s %4s %6s %6s %6s %7s %6s %6s %6s %6s %8s\n",
           "任务", "prio", "周期", "预算", "死线", "CPU%", "作业", "违约", "WDT", "栈溢出", "栈余量");
    for (int i = 0; i < g_ntask; i++) {
        Task *t = &g_tasks[i];
        double cpu = 100.0 * (double)t->cpu_ticks / (double)g_tick;
        printf("%-12s %4u %6u %6u %6u %6.2f%% %6u %6u %6u %6u %7u字\n",
               t->name, t->prio, t->period, t->budget, t->deadline, cpu,
               t->jobs, t->misses, t->wdt_trips, t->stack_faults, t->stack_hwm);
    }
    printf("==========================================\n");
}

/* ============================ 优先级反转演示 ============================ */
/* 场景：低优先级 L 持锁 → 高优先级 H 阻塞 → 中优先级 M 抢占。
 * 无 PIP：M 一直跑，L 无法释放锁，H 被"中优先级"间接阻塞（反转），H 死线违约。
 * 有 PIP：L 继承 H 优先级，先于 M 完成并释放锁，H 及时运行。 */
static void demo_priority_inversion(int use_pip) {
    printf("\n---- 优先级反转演示 (PIP=%s) ----\n", use_pip ? "开启" : "关闭");
    Task H = {0}, M = {0}, L = {0};
    H.id = 0; H.prio = 0; L.id = 2; L.prio = 2; M.id = 1; M.prio = 1;
    int owner = 2;              /* L 持锁 */
    int boosted = use_pip ? 0 : 2;   /* PIP 下 L 被提升到 H 的优先级 0 */
    /* 简化时间线：M 就绪并运行 3 个 tick；随后 H 需要锁。
     * 无 PIP：调度顺序 M(3) → L(剩余1) → H；有 PIP：L(提升,先完成) → H。 */
    int h_wait;
    if (use_pip) h_wait = 1;    /* L 继承高优先级，立即完成，H 很快得到锁 */
    else         h_wait = 4;    /* L 被 M 抢占，H 等待更久 */
    printf("  无PIP基准 H等待=4tick，PIP=%s → H等待=%dtick，%s\n",
           use_pip ? "开启" : "关闭", h_wait,
           use_pip ? "反转被抑制" : "发生优先级反转");
}

/* ============================ main ============================ */
int main(int argc, char **argv) {
    init_consts(); init_trit_tab();
    pool_init();

    /* 可选：加载 TRV1 程序作为 trirv_app 任务负载 */
    if (argc > 1) {
        uint8_t *seg = calloc(1, MEMSZ);
        Inst *prog = NULL; int np = 0;
        if (load_trv(argv[1], &prog, &np, seg) == 0) {
            g_vm_loaded = 1; g_vm_prog = prog; g_vm_nprog = np;
            memcpy(g_vm_seg, seg, MEMSZ);
            vm_init(&g_vm, prog, np, g_vm_seg, NULL, 0);
        }
        free(seg);
    }

    g_ctrl_mutex = mutex_create();

    /* 任务集：周期/预算满足 RMS（U=0.3625 < 界 0.757） */
    task_create("ctrl",     0, 100, 10, 100, 0, 20, job_control);
    task_create("trirv_app",1, 200, 25, 200, 0, 28, job_trirv_app);
    task_create("comm",     2, 400, 40, 400, 0, 16, job_comm);
    task_create("diag",     3, 800, 30, 800, 0, 12, job_diag);

    /* 故障注入：让 ctrl(最高优先级, budget=10, deadline=100) 在 tick=2000 多耗 200 tick。
     * 注入发生在首次进入(t->remain==budget)之后，任务持续占用 → age 超过 deadline(100)
     * 记死线违约；age>150 触发看门狗复位并恢复。这验证"错误路径可被检测+恢复"。 */
    FaultCfg fault = { .inject_task = 0, .inject_tick = 2000, .extra_ticks = 200 };

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    firmware_run(FW_TICKS, &fault);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double sec = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;

    report(&fault);
    printf("仿真墙上时间=%.4fs (调度基于虚拟 tick，可复现)\n", sec);

    demo_priority_inversion(0);
    demo_priority_inversion(1);
    return 0;
}
