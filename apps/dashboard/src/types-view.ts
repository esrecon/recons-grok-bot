// Which main surface is showing. Split from types.ts so components can import
// it without pulling the API models into every bundle chunk.
export type View = "chats" | "skills" | "routines" | "customize" | "audit" | "settings";
