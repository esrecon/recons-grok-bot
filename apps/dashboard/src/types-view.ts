// Which main surface is showing. Split from types.ts so components can import
// it without pulling the API models into every bundle chunk. "chats" is the
// default (the roster + conversation); the rest are the surfaces we add on top
// of Grok Bot's model.
export type View =
  | "chats"
  | "sessions"
  | "skills"
  | "routines"
  | "customize"
  | "audit"
  | "settings";
