/* tricpp.c —— C++ 前端 C 移植 driver（0.1.2 骨架）。
 * 子命令形态：selftest-* 为各层自检（TDD 入口）；
 * input.cpp output.c 为转译主形态（0.1.12 集成）。
 * L8：产物落套件 tmp/，无 bin 缓存。 */
#include "cpp_types.h"
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "selftest-types"))
        return ctype_selftest();
    fprintf(stderr,
            "usage: tricpp selftest-types | input.cpp output.c (v0.1.2)\n");
    return 2;
}
