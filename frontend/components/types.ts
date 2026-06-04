export type LocalMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  itemId?: string | null;
  createdAt?: string;
};
