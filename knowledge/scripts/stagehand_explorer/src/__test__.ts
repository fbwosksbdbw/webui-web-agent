import assert from "node:assert/strict";

import {
  cascadeCandidateFields,
  formLabelMatches,
  uniqueInteractiveFields,
  type ExploreField,
} from "./explore";

function field(partial: Partial<ExploreField>): ExploreField {
  return {
    label: partial.label || "",
    type: partial.type || "input",
    locator: partial.locator || "",
    isRequired: partial.isRequired || false,
    conditionalRequiredBy: partial.conditionalRequiredBy || [],
    options: partial.options || [],
  };
}

assert.equal(formLabelMatches("客户标识类型", "客户标识"), false);
assert.equal(formLabelMatches(" * 客户标识 ", "客户标识"), true);
assert.equal(formLabelMatches("客 户 标 识", "客户标识"), true);

const interactive = uniqueInteractiveFields([
  field({ label: "客户标识类型", type: "single-select" }),
  field({ label: "客户标识", type: "input" }),
  field({ label: "客户标识类型", type: "single-select", options: ["0:入账账号"] }),
  field({ label: "查询", type: "button" }),
]);
assert.deepEqual(
  interactive.map((item) => `${item.label}:${item.type}:${item.options.length}`),
  ["客户标识:input:0", "客户标识类型:single-select:1"]
);

const cascadeFields = cascadeCandidateFields([
  field({ label: "客户标识类型", type: "single-select" }),
  field({ label: "产品代码", type: "single-select" }),
  field({ label: "客户标识", type: "input", conditionalRequiredBy: ["客户标识类型"] }),
]);
assert.deepEqual(
  cascadeFields.map((item) => item.label),
  ["客户标识类型"]
);

console.log("stagehand explorer helper tests passed");
