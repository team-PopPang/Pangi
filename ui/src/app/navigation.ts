export type NavigationItem = Readonly<{
  label: string;
  path: string;
  available: boolean;
}>;

export const primaryNavigation = [
  { label: "개요", path: "/", available: true },
  { label: "연동", path: "/connections", available: false },
  { label: "도구", path: "/tools", available: false },
  { label: "모델 정책", path: "/model-policies", available: false },
  { label: "메모리", path: "/memory", available: false },
  { label: "스케줄", path: "/schedules", available: false },
  { label: "스킬", path: "/skills", available: false },
  { label: "실행 추적", path: "/runs", available: false },
  { label: "Eval", path: "/evals", available: false },
  { label: "Feedback", path: "/feedback", available: false },
  { label: "API 키", path: "/api-keys", available: false },
  { label: "릴리즈 노트", path: "/releases", available: false },
] as const satisfies readonly NavigationItem[];

export const adminNavigation = [
  { label: "사용자와 역할", path: "/admin/users", available: false },
  { label: "기능 팩", path: "/admin/capability-packs", available: false },
  { label: "공휴일 Calendar", path: "/admin/holiday-calendars", available: false },
  { label: "API 사용 기록", path: "/admin/api-usage", available: false },
  { label: "Audit Log", path: "/admin/audit", available: false },
  { label: "IP 승인", path: "/admin/ip-allowlist", available: false },
] as const satisfies readonly NavigationItem[];
