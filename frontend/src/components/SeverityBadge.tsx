import React from "react";

interface SeverityBadgeProps {
  severity: number;
  animate?: boolean;
}

const SEVERITY_CONFIG: Record<
  number,
  { label: string; bg: string; text: string; ring: string; glow: string }
> = {
  1: {
    label: "Low",
    bg: "bg-emerald-500",
    text: "text-white",
    ring: "ring-emerald-300",
    glow: "shadow-emerald-500/40",
  },
  2: {
    label: "Moderate",
    bg: "bg-green-500",
    text: "text-white",
    ring: "ring-green-300",
    glow: "shadow-green-500/40",
  },
  3: {
    label: "Urgent",
    bg: "bg-amber-500",
    text: "text-white",
    ring: "ring-amber-300",
    glow: "shadow-amber-500/40",
  },
  4: {
    label: "Critical",
    bg: "bg-orange-500",
    text: "text-white",
    ring: "ring-orange-300",
    glow: "shadow-orange-500/50",
  },
  5: {
    label: "Life-Threatening",
    bg: "bg-red-600",
    text: "text-white",
    ring: "ring-red-400",
    glow: "shadow-red-600/60",
  },
};

export const SeverityBadge: React.FC<SeverityBadgeProps> = ({
  severity,
  animate = true,
}) => {
  const config = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG[2];

  return (
    <div
      className={`
        inline-flex flex-col items-center justify-center
        w-28 h-28 rounded-full
        ${config.bg} ${config.text}
        ring-4 ${config.ring}
        shadow-2xl ${config.glow}
        ${animate ? "severity-badge-enter" : ""}
        ${severity >= 4 ? "severity-pulse" : ""}
      `}
    >
      <span className="text-4xl font-black leading-none">{severity}</span>
      <span className="text-xs font-semibold mt-1 tracking-wide uppercase opacity-90">
        {config.label}
      </span>
    </div>
  );
};

export default SeverityBadge;
