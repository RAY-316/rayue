export function getApiBase() {
  if (process.env.NEXT_PUBLIC_API_BASE) {
    return process.env.NEXT_PUBLIC_API_BASE;
  }
  if (typeof window !== "undefined") {
    const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    if (isLocal || window.location.port === "8070") {
      return `${window.location.protocol}//${window.location.hostname}:8071`;
    }
    return "";
  }
  return "";
}

const AUTH_TOKEN_KEY = "rayue_auth_token";

export function getAuthToken() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(AUTH_TOKEN_KEY, token);
  }
}

export function clearAuthToken() {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
  }
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function appendToken(url: string) {
  const token = getAuthToken();
  if (!token) {
    return url;
  }
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

export type User = {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
  updated_at: string;
};

export type AuthResponse = {
  user: User;
  token: string;
  expires_at: string;
};

export type CaptchaChallenge = {
  captcha_id: string;
  image_data_url: string;
  expires_in_seconds: number;
};

export type Workspace = {
  id: string;
  user_id: string;
  name: string;
  limit_bytes: number;
  used_bytes: number;
  available_bytes: number;
  created_at: string;
  updated_at: string;
};

export type WorkspaceFile = {
  id: string | null;
  name: string;
  path: string;
  relative_path: string;
  kind: "input" | "output" | string;
  type: string;
  size: number;
  version: number;
  modified_at: string | null;
};

export type Conversation = {
  id: string;
  user_id?: string | null;
  workspace_id?: string | null;
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
  mentioned_files?: FileMention[];
  created_at: string;
};

export type FileMention = {
  file_id: string;
  name: string;
  relative_path: string;
  kind: string;
  type: string;
  size: number;
  version: number;
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
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const token = getAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return response.json() as Promise<T>;
}

async function errorMessage(response: Response) {
  const text = await response.text();
  if (!text) {
    return response.statusText;
  }
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      return parsed.detail;
    }
    if (parsed.detail && typeof parsed.detail === "object") {
      const message = (parsed.detail as { message?: unknown }).message;
      return typeof message === "string" ? message : JSON.stringify(parsed.detail);
    }
  } catch {
    return text;
  }
  return text;
}

export function me() {
  return request<{ user: User }>("/api/auth/me");
}

export function getCaptcha() {
  return request<CaptchaChallenge>("/api/auth/captcha");
}

export function login(email: string, password: string, force = false) {
  return request<AuthResponse>("/api/auth/password/login", {
    method: "POST",
    body: JSON.stringify({ email, password, force }),
  });
}

export function startRegister(email: string, displayName: string | undefined, captchaId: string, captchaAnswer: string) {
  return request<{ ok: boolean; expires_in_seconds: number; code?: string | null }>("/api/auth/register/start", {
    method: "POST",
    body: JSON.stringify({
      email,
      display_name: displayName,
      captcha_id: captchaId,
      captcha_answer: captchaAnswer,
    }),
  });
}

export function completeRegister(email: string, code: string, password: string, displayName?: string) {
  return request<AuthResponse>("/api/auth/register/complete", {
    method: "POST",
    body: JSON.stringify({ email, code, password, display_name: displayName }),
  });
}

export function startPasswordReset(email: string, captchaId: string, captchaAnswer: string) {
  return request<{ ok: boolean; expires_in_seconds: number; code?: string | null }>("/api/auth/password/reset/start", {
    method: "POST",
    body: JSON.stringify({ email, captcha_id: captchaId, captcha_answer: captchaAnswer }),
  });
}

export function completePasswordReset(email: string, code: string, password: string) {
  return request<AuthResponse>("/api/auth/password/reset/complete", {
    method: "POST",
    body: JSON.stringify({ email, code, password }),
  });
}

export async function logout() {
  const response = await fetch(`${getApiBase()}/api/auth/logout`, {
    method: "POST",
    headers: authHeaders(),
  });
  clearAuthToken();
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
}

export function listWorkspaces() {
  return request<Workspace[]>("/api/workspaces");
}

export function createWorkspace(name?: string) {
  return request<Workspace>("/api/workspaces", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function updateWorkspace(id: string, name: string) {
  return request<Workspace>(`/api/workspaces/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export function listWorkspaceFiles(workspaceId: string, kind = "all") {
  return request<WorkspaceFile[]>(`/api/workspaces/${workspaceId}/files?kind=${encodeURIComponent(kind)}`);
}

export async function deleteWorkspaceFile(workspaceId: string, path: string) {
  const response = await fetch(
    `${getApiBase()}/api/workspaces/${workspaceId}/files?path=${encodeURIComponent(path)}`,
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
}

export function workspaceFileDownloadUrl(workspaceId: string, path: string) {
  return appendToken(`${getApiBase()}/api/workspaces/${workspaceId}/files/download?path=${encodeURIComponent(path)}`);
}

export function listConversations(workspaceId?: string | null) {
  const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  return request<Conversation[]>(`/api/conversations${query}`);
}

export function createConversation(title?: string, workspaceId?: string | null) {
  return request<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title, workspace_id: workspaceId }),
  });
}

export async function deleteConversation(id: string) {
  const response = await fetch(`${getApiBase()}/api/conversations/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
}

export function getConversation(id: string) {
  return request<ConversationDetail>(`/api/conversations/${id}`);
}

export function sendMessage(conversationId: string, content: string, mentionedFiles: WorkspaceFile[] = []) {
  return request<{ message: Message }>(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      content,
      mentioned_files: mentionedFiles.map((file) => ({
        file_id: file.id,
        relative_path: file.relative_path,
        version: file.version,
      })),
    }),
  });
}

export async function stopConversation(conversationId: string) {
  const response = await fetch(`${getApiBase()}/api/conversations/${conversationId}/stop`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
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
  return appendToken(`${getApiBase()}/api/conversations/${conversationId}/artifacts/download?path=${encodeURIComponent(path)}`);
}

export function listUploads(conversationId: string) {
  return request<UploadedFile[]>(`/api/conversations/${conversationId}/uploads`);
}

export async function uploadFiles(conversationId: string, files: FileList | File[]) {
  const form = new FormData();
  Array.from(files).forEach((file) => form.append("files", file));
  const response = await fetch(`${getApiBase()}/api/conversations/${conversationId}/uploads`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return response.json() as Promise<UploadedFile[]>;
}

export function conversationEventsUrl(conversationId: string) {
  return appendToken(`${getApiBase()}/api/conversations/${conversationId}/events`);
}
