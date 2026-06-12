/**
 * Stagehand + Playwright CDP 探勘引擎 v2.0
 *
 * ┌──────────────────────────────────────────────────────────────┐
 * │  设计原则                                                     │
 * │  ① CDP 接管已登录 Chrome — connectOverCDP，只 disconnect      │
 * │  ② 以扫描为主 — fast 模式纯 DOM 扫描，deep 模式 AI 增强       │
 * │  ③ 强制识别 HUI 组件 — .h-select-multiple / .h-tag → 多选     │
 * │  ④ 只吐原生 Locator — 严禁输出 XPath、短 ID、坐标              │
 * └──────────────────────────────────────────────────────────────┘
 *
 * 双引擎架构：
 *   引擎 A：Playwright CDP 接管 + page.evaluate() DOM 扫描（fast 模式，零 AI 开销）
 *   引擎 B：Stagehand extract() AI 语义增强（deep 模式，可选，需 LLM API Key）
 *
 * 表单交互（填写、点击、导航）由 cdp_executor.py 在步骤 1b 完成，本引擎主要负责扫描。
 *
 * 运行：
 *   npm install
 *   npm run explore -- --cdp-url=http://localhost:9222
 */

import * as http from "http";
import * as https from "https";
import * as fs from "fs";
import { z } from "zod";

// ═══════════════════════════════════════════════════════════════
//  类型定义
// ═══════════════════════════════════════════════════════════════

/** 探勘输出的单个字段 */
export interface ExploreField {
  /** 字段中文标签（如 "客户标识"、"查询"） */
  label: string;
  /** 组件类型 */
  type: "input" | "single-select" | "multi-select" | "tree-select" | "button";
  /** 可在 Playwright 中直接执行的原生定位器字符串 */
  locator: string;
  /**
   * 是否必填。
   * - fast 模式：优先读 Vue vm.fields[].isRequired，回退到 CSS class / label *
   * - deep 模式：Stagehand AI 补充后统一用 DOM 结果
   */
  isRequired: boolean;
  /**
   * 关联必填来源。
   * 如"客户标识"依赖"客户标识类型"时，值为 ["客户标识类型"]。
   * 空数组 = 无关联必填或自身直接必填。
   */
  conditionalRequiredBy: string[];
  /** 下拉框选项文本列表（仅 single-select / multi-select 有值） */
  options: string[];
}

/** CDP 连接信息 */
interface CDPTarget {
  webSocketDebuggerUrl: string;
  pageTargetId: string;
  currentUrl: string;
}

/** CLI 参数 */
interface Args {
  cdpUrl: string;
  targetUrl: string | null;
  timeout: number;
  mode: "fast" | "deep";
  llmModel: string;
  output: string | null;
  verbose: boolean;
  help: boolean;
}

// ═══════════════════════════════════════════════════════════════
//  CLI 参数解析
// ═══════════════════════════════════════════════════════════════

function parseArgs(): Args {
  const args: Args = {
    cdpUrl: "http://localhost:9222",
    targetUrl: null,
    timeout: 30_000,
    mode: "deep",
    llmModel: "gpt-4o",
    output: null,
    verbose: false,
    help: false,
  };

  for (const raw of process.argv.slice(2)) {
    const [key, val] = raw.startsWith("--") ? raw.slice(2).split("=", 2) : [raw, ""];
    switch (key) {
      case "cdp-url":        args.cdpUrl   = val; break;
      case "target-url":     args.targetUrl = val; break;
      case "timeout":        args.timeout   = parseInt(val, 10); break;
      case "mode":           args.mode      = val as "fast" | "deep"; break;
      case "llm-model":      args.llmModel  = val; break;
      case "output": case "o": args.output  = val; break;
      case "verbose": case "v": args.verbose = true; break;
      case "help": case "h": args.help      = true; break;
    }
  }
  return args;
}

function printHelp() {
  console.log(`
Stagehand CDP 探勘引擎 v2.0

用法:
  npx ts-node src/explore.ts [选项]

选项:
  --cdp-url=URL       CDP 调试地址 (默认: http://localhost:9222)
  --target-url=URL    目标页面 URL 片段，优先匹配含此片段的页面
  --timeout=MS        连接超时毫秒数 (默认: 30000)
  --mode=MODE         扫描模式:
                        fast = 纯 DOM 扫描，零 AI 开销 (推荐)
                        deep = DOM + Stagehand AI 增强 (需 API Key)
  --llm-model=MODEL   Stagehand LLM 模型 (默认: gpt-4o)
  --output=FILE, -o   输出 JSON 文件路径 (默认: stdout)
  --verbose, -v       详细日志
  --help, -h          显示帮助

示例:
  # 快速扫描（推荐日常使用）
  npx ts-node src/explore.ts --mode=fast

  # AI 增强扫描
  npx ts-node src/explore.ts --mode=deep --llm-model=claude-sonnet-4-20250514

  # 指定 CDP 端口 + 输出文件
  npx ts-node src/explore.ts --cdp-url=http://localhost:9223 -o result.json
`);
}

// ═══════════════════════════════════════════════════════════════
//  日志工具 (全部输出到 stderr，stdout 只吐 JSON)
// ═══════════════════════════════════════════════════════════════

let VERBOSE = false;

const log = {
  info: (...a: any[]) => console.error("[INFO]", ...a),
  debug: (...a: any[]) => VERBOSE && console.error("[DEBUG]", ...a),
  warn: (...a: any[]) => console.error("[WARN]", ...a),
  error: (...a: any[]) => console.error("[ERROR]", ...a),
};

export function normalizeFormLabel(value: string): string {
  return String(value || "").replace(/\*/g, "").replace(/\s+/g, "").trim();
}

export function formLabelMatches(actual: string, expected: string): boolean {
  return normalizeFormLabel(actual) === normalizeFormLabel(expected);
}

export function uniqueInteractiveFields(fields: ExploreField[]): ExploreField[] {
  const seen = new Map<string, ExploreField>();
  for (const field of fields) {
    if (!field.label || field.type === "button") continue;
    const key = `${field.label}::${field.type}`;
    if (seen.has(key)) {
      seen.delete(key);
    }
    seen.set(key, field);
  }
  return [...seen.values()];
}

export function cascadeCandidateFields(fields: ExploreField[]): ExploreField[] {
  const triggerLabels = new Set<string>();
  for (const field of fields) {
    for (const trigger of field.conditionalRequiredBy || []) {
      if (trigger) triggerLabels.add(trigger);
    }
  }
  if (triggerLabels.size === 0) {
    return [];
  }
  return uniqueInteractiveFields(fields).filter(
    (field) => field.type === "single-select" && triggerLabels.has(field.label)
  );
}

async function formItemByExactLabel(page: any, label: string): Promise<any | null> {
  const index = await page.evaluate((target: string) => {
    const normalize = (value: string) =>
      String(value || "").replace(/\*/g, "").replace(/\s+/g, "").trim();
    const expected = normalize(target);
    const formItems = Array.from(document.querySelectorAll(".h-form-item"));
    return formItems.findIndex((item: Element) => {
      const labelEl = item.querySelector(
        ".h-form-item-label, .h-form-label, .h-form-item-label-wrap"
      );
      return normalize(labelEl?.textContent || "") === expected;
    });
  }, label);
  if (index < 0) {
    return null;
  }
  return page.locator(".h-form-item").nth(index);
}

// ═══════════════════════════════════════════════════════════════
//  HTTP 工具
// ═══════════════════════════════════════════════════════════════

function httpGet(url: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith("https") ? https : http;
    mod.get(url, (res) => {
      let data = "";
      res.on("data", (chunk: Buffer) => (data += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(data));
        } catch {
          reject(new Error(`JSON 解析失败: ${data.slice(0, 200)}`));
        }
      });
    }).on("error", reject);
  });
}

// ═══════════════════════════════════════════════════════════════
//  CDP 接管层
// ═══════════════════════════════════════════════════════════════

/**
 * 连接到已运行的 Chrome 浏览器（必须已启用 --remote-debugging-port）
 *
 * 关键约束：绝不关闭浏览器，只断开 CDP 连接
 */
async function connectToBrowser(cdpUrl: string, timeout: number, targetUrl: string | null = null) {
  // 动态导入 Playwright（避免全局依赖）
  const { chromium } = await import("playwright");

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    // Step 1: 获取 WebSocket endpoint（兼容 IPv4/IPv6）
    let versionInfo: any;
    try {
      log.info(`正在探测 CDP 端点: ${cdpUrl}/json/version`);
      versionInfo = await httpGet(`${cdpUrl}/json/version`);
    } catch (firstErr: any) {
      // Windows 上 localhost 可能解析到 IPv6 (::1)，Chrome CDP 只监听 IPv4
      // 自动回退到 127.0.0.1
      const fallbackUrl = cdpUrl.replace("localhost", "127.0.0.1");
      log.warn(`localhost 连接失败，回退到 ${fallbackUrl}`);
      versionInfo = await httpGet(`${fallbackUrl}/json/version`);
      cdpUrl = fallbackUrl;
    }
    const wsEndpoint: string | undefined = versionInfo.webSocketDebuggerUrl;
    if (!wsEndpoint) {
      throw new Error("无法从 /json/version 获取 webSocketDebuggerUrl");
    }
    log.debug("WebSocket endpoint:", wsEndpoint);

    // Step 2: 获取页面列表
    const targets = await httpGet(`${cdpUrl}/json`);
    const pageTargets = targets.filter((t: any) => t.type === "page" && t.url !== "about:blank");
    if (pageTargets.length === 0) {
      throw new Error("浏览器中没有找到可用的页面（type=page）");
    }

    // 选择目标页面：优先匹配 targetUrl 的页面，其次选第一个
    let target = pageTargets[0];
    if (targetUrl) {
      const matched = pageTargets.find((t: any) => t.url && t.url.includes(targetUrl));
      if (matched) {
        target = matched;
        log.info(`按 target-url 匹配页面: ${target.url}`);
      } else {
        log.warn(`未匹配 '${targetUrl}'，使用第一个页面: ${target.url}`);
      }
    } else {
      log.info(`接管页面: ${target.title || target.url}`);
    }

    // Step 3: Playwright CDP 连接
    const browser = await chromium.connectOverCDP(wsEndpoint);
    const context = browser.contexts()[0];
    if (!context) throw new Error("无法获取浏览器上下文");

    // 找到目标页面（优先 URL 匹配，其次用 CDP 探测到的 target）
    const pages = context.pages();
    let page: any;
    if (targetUrl) {
      page = pages.find((p) => p.url() && p.url().includes(targetUrl));
      if (!page) {
        page = pages.find((p) => p.url() === target.url) || pages[pages.length - 1];
        log.warn(`Playwright 上下文未匹配 '${targetUrl}'，回退到: ${page?.url()}`);
      }
    } else {
      page = pages.find((p) => p.url() === target.url) || pages[0];
    }
    if (!page) throw new Error("无法找到目标页面");

    // 等待 DOM 稳定
    await page.waitForLoadState("domcontentloaded").catch(() => {});

    log.info(`连接成功 | 当前 URL: ${page.url()}`);
    return { browser, page, context };
  } finally {
    clearTimeout(timer);
  }
}

// ═══════════════════════════════════════════════════════════════
//  引擎 A: 原生 DOM 扫描（核心引擎）
// ═══════════════════════════════════════════════════════════════

/**
 * 在页面上下文中执行深度 DOM 扫描，提取所有表单字段和操作按钮。
 *
 * 策略：
 *  1. .h-form-item → 提取 label + 判断 input/select/multi-select
 *  2. 按钮选择器 → 提取查询/重置/导出等操作按钮
 *  3. 表格操作列 → 提取详情/预览/查看按钮
 *
 * 所有组件类型判断基于 HUI DOM 特征：
 *  - .h-select-multiple → multi-select
 *  - .h-tag → multi-select（在下拉框上下文中）
 *  - .h-select / .h-select-input → single-select
 *  - .h-date-picker / .h-datepicker → single-select
 *  - .h-cascader / .h-tree-select → single-select
 *  - input:not(.h-select-input) → input
 *  - textarea → input
 */
async function scanDOM(page: any): Promise<ExploreField[]> {
  const scanContext = async (context: any, frameName: string | null) => context.evaluate((frameNameArg: string | null) => {
    const results: Array<{
      label: string;
      domType: string;
      isRequired: boolean;
      selectorHint: string;
      source: "hui" | "ext";
      frameName: string | null;
    }> = [];

    // ─── 扫描 .h-form-item ───
    const formItems = document.querySelectorAll(".h-form-item");
    formItems.forEach((item: Element) => {
      // 可见性过滤：getBoundingClientRect 比 display/visibility 更可靠
      const rect = item.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return;

      // 提取 label
      const labelEl = item.querySelector(
        ".h-form-item-label, .h-form-label, .h-form-item-label-wrap"
      );
      if (!labelEl) return;
      const rawLabel = labelEl.textContent || "";
      const label = rawLabel.replace(/\s*\*?\s*$/, "").trim();
      if (!label || label.length > 30) return;

      // 判断是否必填（CSS class 作为初始值，后续会被 Vue rules 覆盖）
      const isRequired =
        item.classList.contains("h-form-item-required") ||
        !!item.querySelector(".h-form-item-required") ||
        rawLabel.includes("*");

      // ─── 组件类型检测（按优先级） ───
      const hasMultiple =
        !!item.querySelector(".h-select-multiple");
      const hasTags =
        !!item.querySelector(".h-tag");
      const hasSelect =
        !!item.querySelector(
          ".h-select:not(.h-select-multiple), .h-select-input, .h-cascader, .h-tree-select"
        );
      const hasDatePick =
        !!item.querySelector(
          ".h-date-picker, .h-datepicker, .h-range-picker"
        );
      const hasTimePick =
        !!item.querySelector(".h-time-picker, .h-timepicker");
      const hasInput = !!item.querySelector(
        "input:not(.h-select-input):not([type='hidden']):not(.h-time-picker-input)"
      );
      const hasTextarea = !!item.querySelector("textarea");
      // 树形选择弹窗：disabled input + choose-btn 按钮
      const hasTreeSelectModal =
        !!item.querySelector("button.choose-btn") &&
        !!item.querySelector("input[disabled]");
      const hasCheckbox = !!item.querySelector(
        ".h-checkbox-group, input[type='checkbox']"
      );
      const hasRadio = !!item.querySelector(
        ".h-radio-group, input[type='radio']"
      );

      let domType: string;
      if (hasMultiple || (hasSelect && hasTags)) {
        domType = "multi-select";
      } else if (hasSelect) {
        domType = "single-select";
      } else if (hasDatePick) {
        domType = "single-select";
      } else if (hasTimePick) {
        domType = "single-select";
      } else if (hasTreeSelectModal) {
        domType = "tree-select";
      } else if (hasInput || hasTextarea) {
        domType = "input";
      } else if (hasCheckbox) {
        domType = "multi-select";
      } else if (hasRadio) {
        domType = "single-select";
      } else {
        domType = "unknown";
      }

      results.push({
        label,
        domType,
        isRequired,
        selectorHint: label,
        source: "hui",
        frameName: frameNameArg,
      });
    });

    // ─── 扫描旧 Ext 表单项（GYYH 老框架 iframe 页面）───
    const extItems = document.querySelectorAll(".x-form-item");
    extItems.forEach((item: Element) => {
      const rect = item.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return;
      const labelEl = item.querySelector("label, .x-form-item-label");
      const rawLabel = labelEl?.textContent || "";
      const label = rawLabel.replace(/[:：]\s*$/, "").replace(/\*/g, "").trim();
      if (!label || label.length > 30) return;

      const inputs = Array.from(item.querySelectorAll("input:not([type='hidden']), textarea"));
      if (inputs.length === 0) return;
      const isRequired = rawLabel.includes("*") || item.textContent?.includes("*") || false;
      const hasTrigger = !!item.querySelector(".x-form-trigger, .x-form-arrow-trigger, .x-form-date-trigger");
      const hasReadonlyInput = inputs.some((input: any) => input.readOnly);
      const domType = hasTrigger || hasReadonlyInput ? "single-select" : "input";
      results.push({
        label,
        domType,
        isRequired,
        selectorHint: label,
        source: "ext",
        frameName: frameNameArg,
      });
    });

    // ─── 扫描查询区域按钮 ───
    const BUTTON_KEYWORDS =
      /查询|重置|导出|新增|批量|导入|下载|搜索|筛选|展开|收起/;
    const SEEN = new Set<string>();

    const btnSelectors = [
      ".h-btn",
      ".h-button",
      "button",
      "[role='button']",
      ".h-form-item .h-btn",
      ".search-bar .h-btn",
      ".toolbar .h-btn",
    ];

    btnSelectors.forEach((sel) => {
      document.querySelectorAll(sel).forEach((btn: Element) => {
        const text = (btn.textContent || "").trim();
        if (!text || text.length > 15 || SEEN.has(text)) return;
        if (!BUTTON_KEYWORDS.test(text)) return;
        // 排除禁用/隐藏按钮
        const style = window.getComputedStyle(btn as HTMLElement);
        if (style.display === "none" || style.visibility === "hidden") return;
        SEEN.add(text);
        results.push({
          label: text,
          domType: "button",
          isRequired: false,
          selectorHint: text,
          source: "hui",
          frameName: frameNameArg,
        });
      });
    });

    // ─── 扫描旧 Ext 按钮 ───
    document.querySelectorAll(".x-btn button, button, input[type='button']").forEach((btn: Element) => {
      const text = ((btn.textContent || (btn as HTMLInputElement).value || "")).trim();
      if (!text || text.length > 15 || SEEN.has(text)) return;
      if (!BUTTON_KEYWORDS.test(text)) return;
      const style = window.getComputedStyle(btn as HTMLElement);
      if (style.display === "none" || style.visibility === "hidden") return;
      SEEN.add(text);
      results.push({
        label: text,
        domType: "button",
        isRequired: false,
        selectorHint: text,
        source: "ext",
        frameName: frameNameArg,
      });
    });

    // ─── 扫描表格操作列按钮 ───
    const OP_KEYWORDS = /详情|预览|查看|编辑|删除|操作|审核|提交|启用|禁用/;
    const opBtns = document.querySelectorAll(
      ".h-omc-table-btn, .h-table-operation .h-btn, .operation-col .h-btn, .h-table-cell .h-btn"
    );
    opBtns.forEach((btn: Element) => {
      const text = (btn.textContent || "").trim();
      if (!text || text.length > 10 || SEEN.has(text)) return;
      if (!OP_KEYWORDS.test(text)) return;
      const style = window.getComputedStyle(btn as HTMLElement);
      if (style.display === "none" || style.visibility === "hidden") return;
      SEEN.add(text);
      results.push({
        label: text,
        domType: "button",
        isRequired: false,
        selectorHint: text,
        source: "hui",
        frameName: frameNameArg,
      });
    });

    return results;
  }, frameName);

  const contexts: Array<{ context: any; frameName: string | null }> = [{ context: page, frameName: null }];
  try {
    for (const frame of page.frames()) {
      if (frame === page.mainFrame()) continue;
      contexts.push({ context: frame, frameName: frame.name() || null });
    }
  } catch {}

  const rawFields: Array<{
    label: string;
    domType: string;
    isRequired: boolean;
    selectorHint: string;
    source: "hui" | "ext";
    frameName: string | null;
  }> = [];
  for (const item of contexts) {
    try {
      rawFields.push(...await scanContext(item.context, item.frameName));
    } catch (e: any) {
      log.debug(`DOM 扫描 frame 失败 (${item.frameName || 'main'}): ${e.message}`);
    }
  }

  // ─── 转换为 ExploreField 并生成 Playwright locator ───
  return rawFields.map((f: { label: string; domType: string; isRequired: boolean; selectorHint: string; source: "hui" | "ext"; frameName: string | null }) => ({
    label: f.label,
    type: mapDomType(f.domType),
    locator: buildLocator(f.label, mapDomType(f.domType), f.source, f.frameName),
    isRequired: f.isRequired,
    conditionalRequiredBy: [] as string[],
    options: [] as string[],
  }));
}

/**
 * 抓取下拉框选项文本。
 * 对 single-select / multi-select 字段：click 打开下拉 → 读选项 → Escape 关闭。
 * 每个字段 try/catch 包裹，失败不阻断。
 */
async function extractSelectOptions(
  page: any,
  fields: ExploreField[]
): Promise<Map<string, string[]>> {
  const optionMap = new Map<string, string[]>();
  const selectFields = uniqueInteractiveFields(fields).filter(
    (f) => f.type === "single-select" || f.type === "multi-select"
  );

  // 🔧 修复：先点击 trigger 触发懒加载，再从 DOM 提取选项
  // 关键：从当前字段的 .h-form-item 内部找下拉框，避免全局搜索误匹配
  for (const field of selectFields) {
    let options: string[] = [];

    try {
      // Escape 关掉可能残存的下拉
      await page.keyboard.press("Escape");
      await new Promise((r) => setTimeout(r, 200));

      // 点击 trigger 触发选项懒加载（如果失败不阻断，继续尝试提取）
      const formItem = await formItemByExactLabel(page, field.label);
      if (!formItem) {
        log.debug(`Exact form item not found for "${field.label}", skipping option extraction`);
        continue;
      }
      const trigger = formItem.locator(".h-select-input, .h-select-multiple, .h-select").first();

      const triggerCount = await trigger.count();
      if (triggerCount > 0) {
        try {
          await trigger.click({ timeout: 3000 });
          await new Promise((r) => setTimeout(r, 500));
        } catch (clickErr) {
          log.debug(`Click failed for "${field.label}", trying extraction anyway`);
        }
      }

      // 优先从当前字段的 .h-form-item 内部提取选项；HiUI 下拉层常被挂到 body，
      // 内部找不到时再从当前可见的全局 dropdown 兜底提取。
      options = await page.evaluate((label: string) => {
        const isVisible = (el: Element) => {
          const htmlEl = el as HTMLElement;
          return !!(htmlEl.offsetWidth || htmlEl.offsetHeight || htmlEl.getClientRects().length);
        };
        const collectOptionsFromContainers = (containers: Element[]) => {
          return containers
            .flatMap((dd: Element) =>
              Array.from(dd.querySelectorAll("li.h-select-item, .h-select-option, .h-option, li"))
            )
            .map((el: Element) => {
              const title = el.getAttribute("title");
              const text = (el.textContent || "").trim();
              return title || text;
            })
            .filter((t: string) =>
              t.length > 0 &&
              t.length < 200 &&
              !t.includes("条/页") &&
              !t.includes("共 ") &&
              !t.includes("无匹配数据") &&
              !t.includes("加载中") &&
              !t.includes("没有更多数据")
            );
        };
        const formItems = Array.from(document.querySelectorAll(".h-form-item"));
        for (const item of formItems) {
          const labelEl = item.querySelector(".h-form-item-label, .h-form-label, .h-form-item-label-wrap");
          const normalize = (value: string) =>
            String(value || "").replace(/\*/g, "").replace(/\s+/g, "").trim();
          if (!labelEl || normalize(labelEl.textContent || "") !== normalize(label)) continue;

          // 排除小尺寸分页组件
          if (item.querySelector(".h-select-small")) continue;

          // 从 .h-select-dropdown-list 或 .h-select-dropdown-content 中提取选项
          const ddList = item.querySelector(".h-select-dropdown-list");
          const ddContent = item.querySelector(".h-select-dropdown-content");
          const opts = collectOptionsFromContainers([ddList, ddContent].filter(Boolean) as Element[]);
          if (opts.length > 0) return opts;
        }
        const visibleDropdowns = Array.from(
          document.querySelectorAll(".h-select-dropdown-list, .h-select-dropdown-content, .h-select-dropdown, ul.h-select-dropdown")
        ).filter(isVisible);
        const portalOptions = collectOptionsFromContainers(visibleDropdowns);
        if (portalOptions.length > 0) return portalOptions;
        return [];
      }, field.label);

      // Escape 关闭下拉
      await page.keyboard.press("Escape");
      await new Promise((r) => setTimeout(r, 200));

    } catch (e: any) {
      log.warn(`Failed to extract options for "${field.label}": ${e.message}`);
      try { await page.keyboard.press("Escape"); } catch {}
    }

    if (options.length > 0) {
      optionMap.set(field.label, options);
      log.debug(`Extracted ${options.length} options for "${field.label}": ${options.slice(0, 3).join(", ")}...`);
    } else {
      log.warn(`No options captured for "${field.label}"`);
    }
  }
  return optionMap;
}


/**
 * 级联字段探勘：对每个下拉框选择一个选项，检查是否出现新字段。
 *
 * 某些页面的字段是级联的（如「时间选择」→「年份」），需要先选择前置字段才能看到后续字段。
 */
async function exploreCascadeFields(
  page: any,
  existingFields: ExploreField[]
): Promise<ExploreField[]> {
  const newFields: ExploreField[] = [];
  const existingLabels = new Set(existingFields.map(f => f.label));

  // 只探勘 Vue 规则确认过的触发字段，避免普通下拉被重复选择。
  const selectFields = cascadeCandidateFields(existingFields);

  for (const field of selectFields) {
    try {
      // 点击下拉框
      const formItem = await formItemByExactLabel(page, field.label);
      if (!formItem) continue;
      const trigger = formItem.locator(".h-select-input").first();

      const triggerCount = await trigger.count();
      if (triggerCount === 0) continue;

      await trigger.click({ timeout: 3000 });
      await new Promise((r) => setTimeout(r, 500));

      // 选择第一个选项
      const firstOption = page.locator(".h-select-dropdown:visible li").first();
      const optionCount = await firstOption.count();
      if (optionCount === 0) {
        await page.keyboard.press("Escape");
        continue;
      }

      await firstOption.click({ timeout: 2000 });
      await new Promise((r) => setTimeout(r, 1000));

      // 重新扫描表单字段
      const currentFields = await scanDOM(page);

      // 找出新字段
      for (const cf of currentFields) {
        if (!existingLabels.has(cf.label) && cf.type !== "button") {
          newFields.push(cf);
          existingLabels.add(cf.label); // 避免重复
        }
      }

      // 恢复：点击重置按钮或清空选择
      const resetBtn = page.getByRole("button", { name: "重置" });
      if (await resetBtn.count() > 0) {
        await resetBtn.click({ timeout: 2000 });
        await new Promise((r) => setTimeout(r, 500));
      }

    } catch (e: any) {
      log.debug(`级联探勘 "${field.label}" 失败: ${e.message}`);
      try { await page.keyboard.press("Escape"); } catch {}
    }
  }

  return newFields;
}

/** DOM 扫描类型 → 标准类型映射 */
function mapDomType(
  domType: string
): "input" | "single-select" | "multi-select" | "tree-select" | "button" {
  switch (domType) {
    case "multi-select":
      return "multi-select";
    case "single-select":
      return "single-select";
    case "tree-select":
      return "tree-select";
    case "button":
      return "button";
    case "input":
      return "input";
    default:
      return "input";
  }
}

/** 构建稳定的 Playwright locator 字符串 */
function buildLocator(
  label: string,
  type: "input" | "single-select" | "multi-select" | "tree-select" | "button",
  source: "hui" | "ext" = "hui",
  frameName: string | null = null
): string {
  const escaped = escapeForLocator(label);
  const framePrefix = frameName
    ? `page.frameLocator('iframe[name="${escapeForDoubleQuotedSelector(frameName)}"]')`
    : "page";

  if (source === "ext") {
    switch (type) {
      case "button":
        return `${framePrefix}.getByText('${escaped}', { exact: true })`;
      case "single-select":
        return (
          `${framePrefix}.locator('.x-form-item')` +
          `.filter({ hasText: '${escaped}' })` +
          `.locator('input:not([type="hidden"])').first()`
        );
      case "input":
      default:
        return (
          `${framePrefix}.locator('.x-form-item')` +
          `.filter({ hasText: '${escaped}' })` +
          `.locator('input:not([type="hidden"]), textarea').first()`
        );
    }
  }

  switch (type) {
    case "button":
      return `${framePrefix}.getByRole('button', { name: '${escaped}' })`;

    case "input":
      return (
        `${framePrefix}.locator('.h-form-item')` +
        `.filter({ hasText: '${escaped}' })` +
        `.getByRole('textbox')`
      );

    case "single-select":
      return (
        `${framePrefix}.locator('.h-form-item')` +
        `.filter({ hasText: '${escaped}' })` +
        `.locator('.h-select-input')`
      );

    case "multi-select":
      return (
        `${framePrefix}.locator('.h-form-item')` +
        `.filter({ hasText: '${escaped}' })` +
        `.locator('.h-select-multiple')`
      );

    case "tree-select":
      return (
        `${framePrefix}.locator('.h-form-item')` +
        `.filter({ hasText: '${escaped}' })` +
        `.locator('button.choose-btn')`
      );

    default:
      return (
        `${framePrefix}.locator('.h-form-item')` +
        `.filter({ hasText: '${escaped}' })`
      );
  }
}

/** 转义 locator 字符串中的特殊字符 */
function escapeForLocator(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function escapeForDoubleQuotedSelector(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

// ═══════════════════════════════════════════════════════════════
//  Vue 规则读取 + 关联必填探测
// ═══════════════════════════════════════════════════════════════

/**
 * VueFieldInfo: scanVueRules 返回的字段信息
 */
interface VueFieldInfo {
  label: string;
  isRequired: boolean;
  conditionalRequiredBy: string[];
}

/**
 * 从 Vue 组件实例读取表单验证规则，并探测关联必填字段。
 *
 * 流程：
 *  1. 查找页面的 el-form / h-form 的 Vue 实例
 *  2. 读取 vm.fields[] 获取每个字段的 label / prop / isRequired
 *  3. 为每个 isRequired=true 的字段（触发字段），尝试临时设值
 *  4. $nextTick 后检查其他字段的 isRequired 是否变化
 *  5. 变化 → 记录关联关系；不变 → 跳过
 *  6. 恢复触发字段原值
 *
 * 安全措施：
 *  - 如果触发字段有 watcher → 跳过该字段的探测
 *  - 用通用占位值 "X" 而非真实业务值
 *  - try/catch 包裹整个探测过程
 */
async function scanVueRules(
  page: any,
  domFields: ExploreField[]
): Promise<Map<string, VueFieldInfo>> {
  const result = new Map<string, VueFieldInfo>();

  try {
    const vueData = await page.evaluate(() => {
      const form = document.querySelector(".h-form, .el-form");
      if (!form) return null;
      const vm = (form as any).__vue__;
      if (!vm || !vm.fields) return null;

      // 找到页面组件（往上遍历直到 ifmCCxgl* 组件）
      let pageVm = vm;
      while (pageVm && pageVm.$parent) {
        pageVm = pageVm.$parent;
        if (pageVm.$options?.name?.includes("ifmCCxgl")) break;
      }

      // 检查页面组件是否有 watchers（安全阀）
      const hasWatchers: string[] = [];
      if (pageVm && pageVm.$options?.watch) {
        hasWatchers.push(...Object.keys(pageVm.$options.watch));
      }

      // 提取所有可见字段的 Vue 信息
      const fields: Array<{
        prop: string;
        label: string;
        isRequired: boolean;
      }> = [];

      for (const f of vm.fields) {
        if (!f || !f.prop) continue;
        const el = f.$el as HTMLElement | undefined;
        if (!el) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;

        const labelEl = el.querySelector(
          ".h-form-item-label, .h-form-label, .el-form-item__label"
        );
        const rawLabel = labelEl?.textContent || "";
        const label = rawLabel.replace(/\s*\*?\s*$/, "").trim();
        if (!label) continue;

        fields.push({
          prop: f.prop,
          label,
          isRequired: !!f.isRequired,
        });
      }

      return {
        fields,
        hasWatchers,
        modelKeys: vm.model ? Object.keys(vm.model) : [],
      };
    });

    if (!vueData) {
      log.warn("无法读取 Vue 表单实例，回退到 CSS class 检测");
      return result;
    }

    log.debug(
      `Vue 规则读取: ${vueData.fields.length} 个字段, ` +
      `${vueData.fields.filter((f: { isRequired: boolean }) => f.isRequired).length} 个必填`
    );

    // 建立 label → prop 映射（供后续合并使用）
    const labelToProp = new Map<string, string>();
    for (const f of vueData.fields) {
      labelToProp.set(f.label, f.prop);
    }

    // 先写入所有字段的 isRequired（从 Vue 读取）
    for (const f of vueData.fields) {
      const domField = domFields.find((d) => d.label === f.label);
      if (domField) {
        result.set(f.label, {
          label: f.label,
          isRequired: f.isRequired,
          conditionalRequiredBy: [],
        });
      }
    }

    // ─── 关联必填探测 ───
    const requiredFields = vueData.fields.filter((f: { isRequired: boolean }) => f.isRequired);
    const nonRequiredFields = vueData.fields.filter((f: { isRequired: boolean }) => !f.isRequired);

    if (requiredFields.length === 0 || nonRequiredFields.length === 0) {
      return result;
    }

    for (const trigger of requiredFields) {
      // 安全阀：检查触发字段是否有 watcher
      const watchKey = vueData.hasWatchers.find(
        (w: string) => w === trigger.prop || w === `"${trigger.prop}"`
      );
      if (watchKey) {
        log.debug(`跳过探测 "${trigger.label}": 页面有 watcher "${watchKey}"`);
        continue;
      }

      try {
        const probeResult = await page.evaluate(
          ({ triggerProp, nonReqProps }: { triggerProp: string; nonReqProps: string[] }) => {
            const form = document.querySelector(".h-form, .el-form");
            if (!form) return null;
            const vm = (form as any).__vue__;
            if (!vm || !vm.model || !vm.fields) return null;

            const original = vm.model[triggerProp];

            // 设占位值（字符串用 "X"，数字/空用 1）
            const placeholder = typeof original === "number" ? 1 : "X";
            vm.model[triggerProp] = placeholder;

            return new Promise((resolve) => {
              (vm as any).$nextTick(() => {
                const affected: string[] = [];
                for (const prop of nonReqProps) {
                  const field = vm.fields.find((f: any) => f.prop === prop);
                  if (field && field.isRequired) {
                    affected.push(prop);
                  }
                }
                // 恢复原值
                vm.model[triggerProp] = original;
                resolve({ affected, original });
              });
            });
          },
          {
            triggerProp: trigger.prop,
            nonReqProps: nonRequiredFields.map((f: { prop: string }) => f.prop),
          }
        );

        if (probeResult && probeResult.affected.length > 0) {
          for (const affectedProp of probeResult.affected) {
            // prop → label
            const affectedField = vueData.fields.find((f: { prop: string }) => f.prop === affectedProp);
            if (!affectedField) continue;

            const existing = result.get(affectedField.label);
            if (existing) {
              existing.conditionalRequiredBy.push(trigger.label);
            }
          }
          log.info(
            `关联必填: "${trigger.label}" → [${probeResult.affected.join(", ")}]`
          );
        }
      } catch (e: any) {
        log.debug(`探测 "${trigger.label}" 关联字段时异常（已跳过）: ${e.message}`);
      }
    }
  } catch (e: any) {
    log.warn(`Vue 规则读取异常（回退到 CSS class）: ${e.message}`);
  }

  return result;
}

// ═══════════════════════════════════════════════════════════════
//  引擎 B: Stagehand AI 语义增强（可选）
// ═══════════════════════════════════════════════════════════════

/**
 * 用 Stagehand extract() 做 AI 语义扫描，补充 DOM 扫描遗漏的元素。
 *
 * 工作原理：
 *  1. Stagehand 分析页面的 accessibility tree
 *  2. 用 LLM 理解页面结构，找出所有表单字段和按钮
 *  3. 与 DOM 扫描结果合并去重
 *
 * 注意：Stagehand 在 LOCAL + cdpUrl 模式下会连接到现有浏览器。
 * 但为了安全起见，我们先用 Playwright 接管，再把 page 传给 Stagehand。
 */
async function stagehandEnhance(
  page: any,
  domResults: ExploreField[],
  model: string
): Promise<ExploreField[]> {
  let StagehandClass: any;
  try {
    const mod = await import("@browserbasehq/stagehand");
    StagehandClass = mod.Stagehand;
  } catch {
    log.warn(
      "Stagehand 未安装，跳过 AI 增强扫描。安装: npm install @browserbasehq/stagehand"
    );
    return domResults;
  }

  log.info("正在初始化 Stagehand AI 引擎...");

  const stagehand = new StagehandClass({
    env: "LOCAL",
    model: model,
    verbose: 0,
    selfHeal: false,
    domSettleTimeout: 5000,
  });

  try {
    await stagehand.init();
    log.info("Stagehand 初始化完成，开始 AI 语义扫描...");

    // 用 Zod schema 定义 extract 的输出结构
    const fieldSchema = z.object({
      label: z.string().describe("字段的中文标签名"),
      fieldType: z
        .string()
        .describe("'input' | 'select' | 'multi-select' | 'date' | 'textarea'"),
      required: z.boolean().describe("是否带红色星号（必填）"),
    });

    const buttonSchema = z.object({
      label: z.string().describe("按钮显示文字"),
      purpose: z
        .string()
        .describe("'search' | 'reset' | 'export' | 'action'"),
    });

    const extractSchema = z.object({
      fields: z.array(fieldSchema).describe("搜索区域的表单字段列表"),
      buttons: z.array(buttonSchema).describe("搜索区域的操作按钮列表"),
    });

    // 用 extract 提取页面的表单字段和按钮信息
    const extracted = await stagehand.extract(
      `分析当前页面的搜索区域，找出所有表单字段和操作按钮。

       对每个表单字段，返回:
         - label: 字段的中文标签名（如"客户标识"、"产品代码"）
         - fieldType: "input" | "select" | "multi-select" | "date" | "textarea"
         - required: 是否带红色星号（必填）

       对每个操作按钮，返回:
         - label: 按钮显示文字（如"查询"、"重置"）
         - purpose: "search" | "reset" | "export" | "action"

       只返回搜索条件区域的字段。
       忽略表格列头、导航菜单、页脚元素。`,
      extractSchema
    );

    const { fields = [], buttons = [] } = extracted as {
      fields: Array<{
        label: string;
        fieldType: string;
        required?: boolean;
      }>;
      buttons: Array<{ label: string; purpose: string }>;
    };

    log.info(
      `Stagehand AI 扫描完成: ${fields.length} 个字段, ${buttons.length} 个按钮`
    );

    // 合并去重
    const existingLabels = new Set(domResults.map((f) => f.label));
    const merged = [...domResults];
    let newCount = 0;

    for (const field of fields) {
      if (!existingLabels.has(field.label)) {
        const type = mapStagehandType(field.fieldType);
        merged.push({
          label: field.label,
          type,
          locator: buildLocator(field.label, type),
          isRequired: false,
          conditionalRequiredBy: [] as string[],
          options: [] as string[],
        });
        newCount++;
      }
    }

    for (const btn of buttons) {
      if (!existingLabels.has(btn.label)) {
        merged.push({
          label: btn.label,
          type: "button",
          locator: `page.getByRole('button', { name: '${escapeForLocator(btn.label)}' })`,
          isRequired: false,
          conditionalRequiredBy: [] as string[],
          options: [] as string[],
        });
        newCount++;
      }
    }

    if (newCount > 0) {
      log.info(`AI 增强新增 ${newCount} 个 DOM 扫描遗漏的元素`);
    }

    return merged;
  } finally {
    await stagehand.close();
  }
}

function mapStagehandType(
  fieldType: string
): "input" | "single-select" | "multi-select" {
  switch (fieldType?.toLowerCase()) {
    case "multi-select":
    case "multiselect":
      return "multi-select";
    case "select":
    case "date":
    case "textarea":
      return "single-select";
    case "input":
    default:
      return "input";
  }
}

// ═══════════════════════════════════════════════════════════════
//  输出格式化
// ═══════════════════════════════════════════════════════════════

/**
 * 最终后处理：去重 + 排序 + 验证
 */
function finalize(fields: ExploreField[]): ExploreField[] {
  // 按 label 去重（保留最后出现的，即 AI 增强版本优先）
  const seen = new Map<string, ExploreField>();
  for (const f of fields) {
    const key = `${f.label}::${f.type}`;
    seen.set(key, f);
  }

  // 排序：表单字段在前，按钮在后；同类型按 label 排序
  const sorted = [...seen.values()].sort((a, b) => {
    const typeOrder = { input: 0, "single-select": 1, "multi-select": 2, "tree-select": 3, button: 4 };
    const diff = typeOrder[a.type] - typeOrder[b.type];
    return diff !== 0 ? diff : a.label.localeCompare(b.label, "zh-CN");
  });

  // 验证：确保所有 locator 不含 XPath、短 ID、坐标
  for (const f of sorted) {
    if (f.locator.includes("xpath=")) {
      log.warn(`字段 "${f.label}" 的 locator 含 XPath，已标记为需要验证`);
    }
    if (/e\d+\b/.test(f.locator) && !f.locator.includes("getByRole")) {
      log.warn(`字段 "${f.label}" 的 locator 可能含短 ID: ${f.locator}`);
    }
  }

  return sorted;
}

// ═══════════════════════════════════════════════════════════════
//  主入口
// ═══════════════════════════════════════════════════════════════

async function main() {
  const args = parseArgs();
  VERBOSE = args.verbose;

  if (args.help) {
    printHelp();
    process.exit(0);
  }

  let browser: any = null;

  try {
    // ─── Step 1: CDP 接管浏览器 ───
    log.info(`正在连接 CDP: ${args.cdpUrl} (超时 ${args.timeout}ms)${args.targetUrl ? `, 目标: ${args.targetUrl}` : ''}`);
    const conn = await connectToBrowser(args.cdpUrl, args.timeout, args.targetUrl);
    browser = conn.browser;
    const page = conn.page;

    // ─── Step 2: 引擎 A - DOM 扫描 ───
    log.info("执行 DOM 扫描（引擎 A）...");
    const startTime = Date.now();
    let results = await scanDOM(page);
    const domTime = Date.now() - startTime;
    log.info(`DOM 扫描完成: ${results.length} 个元素 (${domTime}ms)`);

    // ─── Step 2.5: Vue 验证规则 + 关联必填探测 ───
    log.info("读取 Vue 验证规则 + 关联必填探测...");
    const vueStart = Date.now();
    const vueRules = await scanVueRules(page, results);
    const vueTime = Date.now() - vueStart;
    log.info(`Vue 规则读取完成 (${vueTime}ms)`);

    // 合并：Vue 规则覆盖 CSS class 检测的 isRequired，填入 conditionalRequiredBy
    let vueOverrides = 0;
    for (const field of results) {
      const vueInfo = vueRules.get(field.label);
      if (vueInfo) {
        if (field.isRequired !== vueInfo.isRequired) {
          vueOverrides++;
        }
        field.isRequired = vueInfo.isRequired;
        field.conditionalRequiredBy = vueInfo.conditionalRequiredBy;
      }
    }
    if (vueOverrides > 0) {
      log.info(`Vue 规则覆盖了 ${vueOverrides} 个字段的 isRequired 状态`);
    }

    // ─── Step 3: 引擎 B - Stagehand AI 增强（仅 deep 模式）───
    if (args.mode === "deep") {
      log.info("执行 Stagehand AI 增强扫描（引擎 B）...");
      const aiStart = Date.now();
      results = await stagehandEnhance(page, results, args.llmModel);
      const aiTime = Date.now() - aiStart;
      log.info(`AI 增强完成: ${results.length} 个元素 (${aiTime}ms)`);
    }

    // 交互前先去重，避免同一字段在抓选项和级联探勘阶段被重复点击。
    results = finalize(results);

    // ─── Step 3.5: 抓取下拉选项 ───
    log.info("抓取下拉选项...");
    const optsStart = Date.now();
    const optionsMap = await extractSelectOptions(page, results);
    const optsTime = Date.now() - optsStart;
    let optsTotal = 0;
    for (const field of results) {
      const opts = optionsMap.get(field.label);
      if (opts && opts.length > 0) {
        field.options = opts;
        optsTotal += opts.length;
      }
    }
    log.info(`下拉选项抓取完成: ${optionsMap.size} 个下拉框, ${optsTotal} 个选项 (${optsTime}ms)`);

    // ─── Step 3.6: 级联字段探勘 ───
    // 对每个下拉框选择一个选项，检查是否出现新字段
    log.info("探勘级联字段...");
    const cascadeStart = Date.now();
    const cascadeFields = await exploreCascadeFields(page, results);
    if (cascadeFields.length > 0) {
      // 合并级联字段到结果中
      for (const cf of cascadeFields) {
        if (!results.some(r => r.label === cf.label)) {
          results.push(cf);
          log.info(`发现级联字段: ${cf.label}`);
        }
      }
      log.info(`级联字段探勘完成: 发现 ${cascadeFields.length} 个新字段`);
    } else {
      log.info("未发现级联字段");
    }
    const cascadeTime = Date.now() - cascadeStart;

    // ─── Step 4: 后处理 ───
    results = finalize(results);

    // ─── Step 5: 输出 ───
    const json = JSON.stringify(results, null, 2);

    if (args.output) {
      fs.writeFileSync(args.output, json, "utf-8");
      log.info(`结果已写入: ${args.output}`);
    } else {
      // stdout 只吐 JSON，stderr 吐日志
      process.stdout.write(json + "\n");
    }

    // 统计
    const stats = {
      total: results.length,
      inputs: results.filter((f) => f.type === "input").length,
      singleSelects: results.filter((f) => f.type === "single-select").length,
      multiSelects: results.filter((f) => f.type === "multi-select").length,
      buttons: results.filter((f) => f.type === "button").length,
      required: results.filter((f) => f.isRequired).length,
      conditionalRequired: results.filter((f) => f.conditionalRequiredBy.length > 0).length,
    };
    log.info(
      `探勘完成 | 总计: ${stats.total} | ` +
        `输入框: ${stats.inputs} | 单选: ${stats.singleSelects} | ` +
        `多选: ${stats.multiSelects} | 按钮: ${stats.buttons} | ` +
        `必填: ${stats.required} | 关联必填: ${stats.conditionalRequired}`
    );
  } catch (err: any) {
    log.error(`探勘失败: ${err.message}`);
    if (VERBOSE) console.error(err.stack);
    process.exit(1);
  } finally {
    // ★★★ 关键约束：绝不关闭浏览器，只断开 CDP 连接 ★★★
    if (browser) {
      try {
        browser.disconnect();
        log.info("CDP 连接已断开（浏览器保持运行）");
      } catch (e: any) {
        log.debug("断开连接时异常（可忽略）:", e.message);
      }
    }
    // Playwright CDP 连接可能残留 WebSocket 句柄导致进程不退出，强制退出
    process.exit(0);
  }
}

if (require.main === module) {
  main();
}
