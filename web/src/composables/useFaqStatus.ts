/** FAQ / KB 文档状态 -> Element Plus tag type + 中文 label
 *
 *  - type 字段直接传给 <el-tag :type="...">
 *  - text  用于 <el-tag>{{ text }}</el-tag>
 *  - 未知状态降级为 info / 原值, 避免 UI 异常
 */

interface StatusDisplay {
  type: "" | "success" | "warning" | "danger" | "info"
  text: string
}

const FAQ_STATUS_MAP: Record<string, StatusDisplay> = {
  DRAFT:     { type: "info",    text: "草稿" },
  IN_REVIEW: { type: "warning", text: "审核中" },
  APPROVED:  { type: "",        text: "已通过" },
  PUBLISHED: { type: "success", text: "已发布" },
  REJECTED:  { type: "danger",  text: "已驳回" },
  SUPERSEDED:{ type: "info",    text: "已取代" },
  ARCHIVED:  { type: "info",    text: "已归档" },
}

export function useFaqStatus() {
  function tagType(status: string | undefined): StatusDisplay["type"] {
    return (status && FAQ_STATUS_MAP[status]?.type) ?? "info"
  }
  function text(status: string | undefined): string {
    return (status && FAQ_STATUS_MAP[status]?.text) ?? status ?? "未知"
  }
  return { tagType, text }
}
