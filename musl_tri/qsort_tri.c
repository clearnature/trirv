/* qsort —— musl src/stdlib/qsort.c 的三进制原生改编。
 *
 * 与上游（musl 1.2.5 smoothsort）的差异：
 *   1. 算法替换：上游 smoothsort（Leonardo 堆，pshift 位掩码状态机
 *      `(p[0]&3)==3`、lp[] Leonardo 数表）→ 插入排序（小段）+ 归并排序
 *      （大段）混合。语义等价：qsort 只要求「按 cmp 回调排序」，不要求
 *      特定算法；C 标准明确 qsort 不保证稳定性
 *   2. 位掩码消除：smoothsort 的 pshift 状态依赖 `& 3` 二进制掩码——
 *      TriRV 的 & 是逐 trit 三值逻辑，语义不同。算法替换后此状态不存在
 *   3. 寄存器压力消除：smoothsort 局部状态多（触发 TriRV C 后端
 *      "位运算需要 5 个空闲 callee-saved"报错）；归并实现局部变量少
 *   4. 比较回调签名保持 `int (*)(const void*, const void*)`（qsort_nr
 *      的 wrapper 模式依赖此签名）
 * width==4（int）走专门快路径（直接按 int 比较交换），其余 width 走
 * memcmp 语义的逐字节路径。
 */
int memcmp(const void *a, const void *b, int n);
void *memcpy(void *d, const void *s, int n);

/* ---- 归并排序核心（width=4 int 专版）----
 * 工作区用全局数组（TriRV 单线程语义；上游 smoothsort 也用静态 shift 表）*/
int qs_tmp_int[256];

void msort_int(int *arr, int *tmp, int lo, int hi, int (*cmp)(const void *, const void *))
{
	if (hi - lo <= 8) {
		/* 小段插入排序 */
		int i;
		for (i = lo + 1; i < hi; i++) {
			int v = arr[i];
			int j = i - 1;
			while (j >= lo && cmp(&arr[j], &v) > 0) {
				arr[j + 1] = arr[j];
				j--;
			}
			arr[j + 1] = v;
		}
		return;
	}
	{
		int mid = (lo + hi) / 2;
		int i = lo, j = mid, k = lo;
		msort_int(arr, tmp, lo, mid, cmp);
		msort_int(arr, tmp, mid, hi, cmp);
		while (i < mid && j < hi) {
			if (cmp(&arr[i], &arr[j]) <= 0) tmp[k++] = arr[i++];
			else tmp[k++] = arr[j++];
		}
		while (i < mid) tmp[k++] = arr[i++];
		while (j < hi) tmp[k++] = arr[j++];
		for (k = lo; k < hi; k++) arr[k] = tmp[k];
	}
}

unsigned char qs_tmp_wide[512];

void msort_wide(unsigned char *arr, unsigned char *tmp, int nel, int width,
                int (*cmp)(const void *, const void *))
{
	if (nel <= 8) {
		int i;
		for (i = 1; i < nel; i++) {
			unsigned char v[64];
			memcpy(v, arr + (i)*width, width);
			{
				int j = i - 1;
				while (j >= 0 && cmp(arr + j * width, v) > 0) {
					memcpy(arr + (j + 1) * width, arr + j * width, width);
					j--;
				}
				memcpy(arr + (j + 1) * width, v, width);
			}
		}
		return;
	}
	{
		int mid = nel / 2;
		int i = 0, j = mid, k = 0;
		msort_wide(arr, tmp, mid, width, cmp);
		msort_wide(arr + mid * width, tmp + mid * width, nel - mid, width, cmp);
		while (i < mid && j < nel) {
			if (cmp(arr + i * width, arr + j * width) <= 0) {
				memcpy(tmp + k * width, arr + i * width, width);
				i++; k++;
			} else {
				memcpy(tmp + k * width, arr + j * width, width);
				j++; k++;
			}
		}
		while (i < mid) { memcpy(tmp + k * width, arr + i * width, width); i++; k++; }
		while (j < nel) { memcpy(tmp + k * width, arr + j * width, width); j++; k++; }
		for (k = 0; k < nel; k++) memcpy(arr + k * width, tmp + k * width, width);
	}
}

void qsort(void *base, int nel, int width, int (*cmp)(const void *, const void *))
{
	if (nel <= 1) return;
	if (width == 4) {
		msort_int(base, qs_tmp_int, 0, nel, cmp);
	} else {
		msort_wide(base, qs_tmp_wide, nel, width, cmp);
	}
}
