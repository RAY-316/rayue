export function getApiBase() {
  if (process.env.NEXT_PUBLIC_API_BASE) {
    return process.env.NEXT_PUBLIC_API_BASE;
  }
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8071`;
  }
  return "";
}

export type Conversation = {
  id: string;
  title: string;
  status: string;
  sandbox_id: string | null;
  codex_thread_id: string | null;
  workspace_path: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  last_activity_at: string;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | string;
  content: string;
  item_id: string | null;
  created_at: string;
};

export type AgentEvent = {
  id?: string;
  conversation_id?: string;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type AgentTurn = {
  id: string;
  conversation_id: string;
  user_message_id: string | null;
  status: string;
  phase: string;
  error: string | null;
  artifact_count: number;
  started_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type ConversationDetail = {
  conversation: Conversation;
  messages: Message[];
  events: AgentEvent[];
  turns: AgentTurn[];
};

export type Skill = {
  name: string;
  path: string;
  bytes: number;
  updated_at: string;
};

export type Artifact = {
  name: string;
  path: string;
  relative_path: string;
  type: string;
  size: number;
  modified_at: string | null;
  turn_id?: string | null;
  first_seen_at?: string | null;
};

export type UploadedFile = {
  name: string;
  path: string;
  relative_path: string;
  size: number;
  modified_at: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json() as Promise<T>;
}

export function listConversations() {
  return request<Conversation[]>("/api/conversations");
}

export function createConversation(title?: string) {
  return request<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export async function deleteConversation(id: string) {
  const response = await fetch(`${getApiBase()}/api/conversations/${id}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
}

export function getConversation(id: string) {
  return request<ConversationDetail>(`/api/conversations/${id}`);
}

export function sendMessage(conversationId: string, content: string) {
  return request<{ message: Message }>(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function stopConversation(conversationId: string) {
  const response = await fetch(`${getApiBase()}/api/conversations/${conversationId}/stop`, {
    method: "POST",
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
}

export function listSkills() {
  return request<Skill[]>("/api/admin/skills");
}

export function putSkill(name: string, content: string) {
  return request<Skill>(`/api/admin/skills/${name}`, {
    method: "PUT",
    body: JSON.stringify({ name, content }),
  });
}

export function listArtifacts(conversationId: string) {
  return request<Artifact[]>(`/api/conversations/${conversationId}/artifacts`);
}

export function artifactDownloadUrl(conversationId: string, path: string) {
  return `${getApiBase()}/api/conversations/${conversationId}/artifacts/download?path=${encodeURIComponent(path)}`;
}

export function listUploads(conversationId: string) {
  return request<UploadedFile[]>(`/api/conversations/${conversationId}/uploads`);
}

export async function uploadFiles(conversationId: string, files: FileList | File[]) {
  const form = new FormData();
  Array.from(files).forEach((file) => form.append("files", file));
  const response = await fetch(`${getApiBase()}/api/conversations/${conversationId}/uploads`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json() as Promise<UploadedFile[]>;
}
