import { shapeForId } from "../theme";

// Grok Bot's signature: a saturated organic blob with two white "eyes". Shape is
// stable per agent id; colour comes from the agent's avatar_color.
export function BotAvatar({
  id,
  color,
  size = 36,
  title,
}: {
  id: string;
  color: string;
  size?: number;
  title?: string;
}) {
  const path = shapeForId(id);
  // Eyes: two rounded oblongs, angled slightly inward for the "shy" look.
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      role="img"
      aria-label={title ? `${title} avatar` : "agent avatar"}
      className="shrink-0"
    >
      <path d={path} fill={color} />
      <g fill="#ffffff">
        <rect x="34" y="44" width="7" height="16" rx="3.5" transform="rotate(-8 37.5 52)" />
        <rect x="59" y="44" width="7" height="16" rx="3.5" transform="rotate(8 62.5 52)" />
      </g>
    </svg>
  );
}

// Status is carried by a small dot in the corner (running/paused/error), echoing
// the app's text-forward status language without inventing loud chips.
export function BotAvatarWithStatus(props: {
  id: string;
  color: string;
  size?: number;
  status?: "running" | "paused" | "error";
  title?: string;
}) {
  const { status = "running", size = 36 } = props;
  const dot =
    status === "running"
      ? "#2fbf5f"
      : status === "paused"
        ? "#8e8e93"
        : "#e5484d";
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <BotAvatar {...props} />
      <span
        className="absolute bottom-0 right-0 block rounded-full ring-2 ring-bg"
        style={{ width: size * 0.28, height: size * 0.28, background: dot }}
        aria-hidden
      />
    </div>
  );
}
