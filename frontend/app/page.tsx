"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Code2,
  Download,
  FileText,
  FileUp,
  Lightbulb,
  Loader2,
  Trash2,
  MessageSquarePlus,
  Paperclip,
  Plus,
  RefreshCw,
  Send,
  Settings,
  Sparkles,
  Square,
  SquareTerminal,
  Wrench,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import {
  AgentEvent,
  AgentTurn,
  Artifact,
  Conversation,
  Message,
  Skill,
  UploadedFile,
  artifactDownloadUrl,
  createConversation,
  deleteConversation,
  getApiBase,
  getConversation,
  listArtifacts,
  listConversations,
  listSkills,
  listUploads,
  putSkill,
  sendMessage,
  stopConversation,
  uploadFiles,
} from "@/lib/api";
import type { LocalMessage } from "@/components/types";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

const statusLabel: Record<string, string> = {
  idle: "就绪",
  queued: "排队中",
  running: "处理中",
  stopping: "停止中",
  error: "出错",
};

const MAX_UPLOAD_FILES = 10;
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const PROCESS_DETAIL_LIMIT = 360;
const MAX_EVENT_HISTORY = 3000;
const INLINE_ARTIFACT_LIMIT = 12;

function messageFromApi(message: Message): LocalMessage {
  return {
    id: message.id,
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content,
    itemId: message.item_id,
    createdAt: message.created_at,
  };
}

function mergeMessages(current: LocalMessage[], incoming: LocalMessage[]) {
  const incomingById = new Map(incoming.map((message) => [message.id, message]));
  const merged = new Map(incomingById);
  for (const message of current) {
    if (!message.id.startsWith("local-user-")) {
      continue;
    }
    const alreadyPersisted = incoming.some(
      (incomingMessage) =>
        incomingMessage.role === message.role &&
        incomingMessage.content === message.content &&
        Math.abs(timestamp(incomingMessage.createdAt ?? "") - timestamp(message.createdAt ?? "")) < 120_000,
    );
    if (!alreadyPersisted) {
      merged.set(message.id, message);
    }
  }
  return Array.from(merged.values()).sort(
    (left, right) => timestamp(left.createdAt ?? "") - timestamp(right.createdAt ?? ""),
  );
}

function getPayloadString(payload: Record<string, unknown>, key: string): string | undefined {
  const value = payload[key];
  return typeof value === "string" ? value : undefined;
}

function normalizeItemId(payload: Record<string, unknown>): string | undefined {
  return getPayloadString(payload, "itemId") ?? getPayloadString(payload, "item_id");
}

type ProcessStep = {
  id: string;
  kind: "reasoning" | "command" | "file" | "setup" | "result" | "error";
  title: string;
  detail?: string;
  status?: "running" | "done" | "error";
};

type ActiveProcess = {
  kind: "reasoning" | "command" | "network" | "file" | "setup" | "error";
  title: string;
  detail?: string;
  status?: "running" | "done" | "error";
};

type ProcessGroup = {
  id: string;
  createdAt: string;
  completedAt?: string;
  status: "running" | "done" | "error";
  reasoning: string;
  active?: ActiveProcess;
  steps: ProcessStep[];
  commandCount: number;
  failedCommandCount: number;
};

type ArtifactGroup = {
  id: string;
  createdAt: string;
  turnId?: string;
  files: Artifact[];
  previewPaths: Record<string, string>;
};

type TimelineMeta = {
  createdAt: string;
  sortTime: number;
  order: number;
};

type TimelineItem =
  | ({ type: "message"; message: LocalMessage } & TimelineMeta)
  | ({ type: "process"; group: ProcessGroup } & TimelineMeta)
  | ({ type: "artifacts"; group: ArtifactGroup } & TimelineMeta);

function buildTimeline(
  messages: LocalMessage[],
  groups: ProcessGroup[],
  artifactGroups: ArtifactGroup[] = [],
  turns: AgentTurn[] = [],
): TimelineItem[] {
  const sortedMessages = [...messages].sort(
    (left, right) => timestamp(left.createdAt ?? "") - timestamp(right.createdAt ?? ""),
  );
  const turnsById = new Map(turns.map((turn) => [turn.id, turn]));
  const messageTimeById = new Map(
    sortedMessages.map((message) => [message.id, timestamp(message.createdAt ?? "")]),
  );
  const userMessages = sortedMessages.filter((message) => message.role === "user");
  const userTimes = userMessages
    .map((message) => timestamp(message.createdAt ?? ""))
    .filter(Number.isFinite);
  const assistantTimes = sortedMessages
    .filter((message) => message.role === "assistant")
    .map((message) => timestamp(message.createdAt ?? ""))
    .filter(Number.isFinite);
  const processById = new Map(groups.map((group) => [group.id, group]));

  const items: TimelineItem[] = [
    ...sortedMessages.map((message, index) => ({
      type: "message" as const,
      message,
      createdAt: message.createdAt ?? "",
      sortTime: timestamp(message.createdAt ?? ""),
      order: index * 10 + (message.role === "user" ? 0 : 4),
    })),
    ...groups.map((group, index) => ({
      type: "process" as const,
      group,
      createdAt: group.createdAt,
      sortTime: processTimelineTime(group, userMessages, messageTimeById, turnsById),
      order: index * 10 + 1,
    })),
    ...artifactGroups.map((group) => {
      const createdAt = artifactTimelineTime(
        group,
        assistantTimes,
        userTimes,
        processById,
        turnsById,
        messageTimeById,
      );
      return {
        type: "artifacts" as const,
        group,
        createdAt,
        sortTime: timestamp(createdAt),
        order: 8,
      };
    }),
  ];

  return items.sort((left, right) => left.sortTime - right.sortTime || left.order - right.order);
}

function processTimelineTime(
  group: ProcessGroup,
  userMessages: LocalMessage[],
  messageTimeById: Map<string, number>,
  turnsById: Map<string, AgentTurn>,
) {
  const turn = turnsById.get(group.id);
  const turnUserTime = turn?.user_message_id ? messageTimeById.get(turn.user_message_id) : undefined;
  if (typeof turnUserTime === "number" && Number.isFinite(turnUserTime)) {
    return turnUserTime + 1;
  }

  const groupStart = timestamp(group.createdAt);
  const groupEnd = group.completedAt ? timestamp(group.completedAt) : groupStart;
  const anchor = Math.max(groupStart, groupEnd);
  const previousUser = [...userMessages]
    .reverse()
    .find((message) => timestamp(message.createdAt ?? "") <= anchor);
  if (previousUser) {
    return timestamp(previousUser.createdAt ?? "") + 1;
  }

  const nextUser = userMessages.find((message) => {
    const userTime = timestamp(message.createdAt ?? "");
    return userTime >= groupStart && userTime - groupStart < 120_000;
  });
  return nextUser ? timestamp(nextUser.createdAt ?? "") + 1 : groupStart;
}

function artifactTimelineTime(
  group: ArtifactGroup,
  assistantTimes: number[],
  userTimes: number[],
  processById: Map<string, ProcessGroup>,
  turnsById: Map<string, AgentTurn>,
  messageTimeById: Map<string, number>,
) {
  const artifactTime = timestamp(group.createdAt);
  if (group.turnId) {
    const process = processById.get(group.turnId);
    const turn = turnsById.get(group.turnId);
    const turnUserTime = turn?.user_message_id ? messageTimeById.get(turn.user_message_id) : undefined;
    const processStart = process ? timestamp(process.createdAt) : undefined;
    const userTime =
      typeof turnUserTime === "number" && Number.isFinite(turnUserTime)
        ? turnUserTime
        : processStart;
    if (typeof userTime === "number" && Number.isFinite(userTime)) {
      const nextUser = userTimes.find((time) => time > userTime);
      const followingAssistant = assistantTimes
        .filter((assistantTime) => assistantTime > userTime && (!nextUser || assistantTime < nextUser))
        .at(-1);
      if (followingAssistant) {
        return new Date(followingAssistant + 1).toISOString();
      }
      if (process?.completedAt) {
        return new Date(timestamp(process.completedAt) + 1).toISOString();
      }
      return new Date(userTime + 2).toISOString();
    }
  }
  const nextUser = userTimes.find((userTime) => userTime > artifactTime);
  const followingAssistant = assistantTimes.find(
    (assistantTime) => assistantTime >= artifactTime && (!nextUser || assistantTime < nextUser),
  );
  if (!followingAssistant) {
    return group.createdAt;
  }
  return new Date(followingAssistant + 1).toISOString();
}

function buildArtifactGroups(
  artifacts: Artifact[],
  events: AgentEvent[],
  turns: AgentTurn[] = [],
  processGroups: ProcessGroup[] = [],
): ArtifactGroup[] {
  const previewPaths = buildPreviewPathMap(artifacts);
  const turnsById = new Map(turns.map((turn) => [turn.id, turn]));
  const processById = new Map(processGroups.map((group) => [group.id, group]));
  const artifactsByPath = new Map<string, Artifact>();
  const artifactTurnByPath = new Map<string, string>();
  const artifactEventTimeByTurn = new Map<string, string>();

  for (const event of events) {
    if (event.type !== "artifacts_updated") {
      continue;
    }
    const eventTurnId = getTurnId(event);
    if (eventTurnId && event.created_at) {
      artifactEventTimeByTurn.set(
        eventTurnId,
        latestDateString([artifactEventTimeByTurn.get(eventTurnId), event.created_at]) ?? event.created_at,
      );
    }
    const eventArtifacts = getPayloadArray(event.payload, "changed_artifacts")
      .concat(getPayloadArray(event.payload, "artifacts"))
      .map(artifactFromPayload)
      .filter((artifact): artifact is Artifact => Boolean(artifact));

    for (const artifact of eventArtifacts) {
      const turnId = artifact.turn_id ?? eventTurnId;
      if (turnId) {
        artifactTurnByPath.set(artifact.relative_path, turnId);
      }
      artifactsByPath.set(artifact.relative_path, {
        ...artifact,
        turn_id: turnId ?? artifact.turn_id,
        first_seen_at: artifact.first_seen_at ?? event.created_at ?? null,
      });
    }
  }

  for (const artifact of artifacts) {
    const eventTurnId = artifactTurnByPath.get(artifact.relative_path);
    artifactsByPath.set(artifact.relative_path, {
      ...artifact,
      turn_id: artifact.turn_id ?? eventTurnId,
    });
  }

  const filesByTurn = new Map<string, Artifact[]>();
  selectPanelArtifacts(Array.from(artifactsByPath.values())).forEach((artifact) => {
    const turnId = artifact.turn_id ?? artifactTurnByPath.get(artifact.relative_path);
    if (turnId && !isArtifactTurnSettled(turnId, turnsById, processById)) {
      return;
    }
    const key = turnId ?? "unassigned";
    filesByTurn.set(key, [
      ...(filesByTurn.get(key) ?? []),
      turnId && !artifact.turn_id ? { ...artifact, turn_id: turnId } : artifact,
    ]);
  });

  const groups: ArtifactGroup[] = [];
  filesByTurn.forEach((files, key) => {
    const visible = selectInlineArtifacts(files);
    if (visible.length === 0) {
      return;
    }
    const turnId = key === "unassigned" ? undefined : key;
    const createdAt =
      latestDateString([
        turnId ? artifactEventTimeByTurn.get(turnId) : undefined,
        ...visible.map((artifact) => artifact.first_seen_at ?? artifact.modified_at),
      ]) ?? new Date().toISOString();
    groups.push({
      id: `artifacts-turn-${key}-${visible.map((artifact) => `${artifact.relative_path}:${artifact.size}`).join("|")}`,
      createdAt,
      turnId,
      files: visible,
      previewPaths,
    });
  });

  return groups.sort((left, right) => timestamp(left.createdAt) - timestamp(right.createdAt));
}

function latestDateString(values: (string | null | undefined)[]) {
  const latest = values
    .filter((value): value is string => Boolean(value))
    .map((value) => [value, timestamp(value)] as const)
    .filter(([, time]) => Number.isFinite(time))
    .sort((left, right) => left[1] - right[1])
    .at(-1);
  return latest?.[0];
}

function isArtifactTurnSettled(
  turnId: string,
  turnsById: Map<string, AgentTurn>,
  processById: Map<string, ProcessGroup>,
) {
  const process = processById.get(turnId);
  if (process) {
    return process.status !== "running";
  }
  const turn = turnsById.get(turnId);
  if (!turn) {
    return true;
  }
  return !["queued", "running", "stopping"].includes(turn.status);
}

function buildProcessGroups(events: AgentEvent[]): ProcessGroup[] {
  const groups: ProcessGroup[] = [];
  const groupsByTurn = new Map<string, ProcessGroup>();
  const pendingSetup: AgentEvent[] = [];
  let current: ProcessGroup | null = null;

  function createGroup(turnId: string, event: AgentEvent) {
    const existing = groupsByTurn.get(turnId);
    if (existing) {
      addEventToProcessGroup(existing, event);
      current = existing;
      return existing;
    }
    const group: ProcessGroup = {
      id: turnId,
      createdAt: event.created_at ?? new Date().toISOString(),
      status: "running",
      reasoning: "",
      active: undefined,
      steps: [],
      commandCount: 0,
      failedCommandCount: 0,
    };
    groups.push(group);
    groupsByTurn.set(turnId, group);
    current = group;
    pendingSetup.splice(0).forEach((setupEvent) => addEventToProcessGroup(group, setupEvent));
    addEventToProcessGroup(group, event);
    return group;
  }

  function rekeyGroup(group: ProcessGroup, turnId: string) {
    if (group.id === turnId) {
      groupsByTurn.set(turnId, group);
      return;
    }
    for (const [key, value] of groupsByTurn.entries()) {
      if (value === group) {
        groupsByTurn.delete(key);
      }
    }
    group.id = turnId;
    groupsByTurn.set(turnId, group);
  }

  for (const event of events) {
    if (event.type === "stream_connected" || event.type === "assistant_delta") {
      continue;
    }
    if (event.type === "turn_started") {
      const turnId = getTurnId(event) ?? `turn-${event.created_at ?? groups.length}`;
      const existingGroup = groupsByTurn.get(turnId);
      if (existingGroup) {
        addEventToProcessGroup(existingGroup, event);
        current = existingGroup;
        continue;
      }
      const activeGroup = current as ProcessGroup | null;
      if (activeGroup && activeGroup.status === "running") {
        rekeyGroup(activeGroup, turnId);
        pendingSetup.splice(0).forEach((setupEvent) => addEventToProcessGroup(activeGroup, setupEvent));
        addEventToProcessGroup(activeGroup, event);
        current = activeGroup;
      } else {
        createGroup(turnId, event);
      }
      continue;
    }
    if (shouldAttachBeforeTurn(event) && !current) {
      pendingSetup.push(event);
      continue;
    }
    const turnId = getTurnId(event);
    let group = (turnId ? groupsByTurn.get(turnId) : current) ?? null;
    if (!group && turnId && current?.status === "running" && isProcessEvent(event)) {
      rekeyGroup(current, turnId);
      group = current;
    }
    if (!group && event.type === "conversation_status") {
      const status = getPayloadString(event.payload, "status");
      if (status !== "queued" && status !== "running" && status !== "stopping") {
        continue;
      }
    }
    if (!group && isProcessEvent(event)) {
      group = createGroup(turnId ?? `process-${event.created_at ?? groups.length}`, event);
      continue;
    }
    if (!group) {
      continue;
    }
    addEventToProcessGroup(group, event);
    if (
      event.type === "conversation_status" &&
      getPayloadString(event.payload, "status") === "idle"
    ) {
      current = null;
    }
  }

  return groups.filter((group) => group.reasoning.trim() || group.steps.length > 0 || group.active);
}

function isProcessEvent(event: AgentEvent) {
  return [
    "artifacts_synced",
    "artifacts_updated",
    "assistant_message",
    "conversation_status",
    "diff_updated",
    "error",
    "input_files_synced",
    "item_completed",
    "item_started",
    "plan_updated",
    "reasoning_delta",
    "sandbox_started",
    "sandbox_starting",
    "skills_synced",
    "thread_started",
    "turn_completed",
    "turn_watchdog",
    "turn_interrupted",
    "turn_recovered",
  ].includes(event.type);
}

function addEventToProcessGroup(group: ProcessGroup, event: AgentEvent) {
  if (event.type === "reasoning_delta") {
    const delta = getPayloadString(event.payload, "delta");
    const method = getPayloadString(event.payload, "method");
    if (delta && method === "item/reasoning/summaryTextDelta") {
      group.reasoning += delta;
    }
    if (group.status === "running" && !group.active) {
      group.active = { kind: "reasoning", title: "思考中..", status: "running" };
    }
    return;
  }

  if (event.type === "item_started" || event.type === "item_completed") {
    const item = getPayloadObject(event.payload, "item");
    if (item?.type === "agentMessage") {
      if (event.type === "item_completed") {
        const text = typeof item.text === "string" ? item.text.trim() : "";
        const itemId = typeof item.id === "string" ? item.id : `${group.id}-agent-${group.steps.length}`;
        if (text) {
          upsertStep(group, {
            id: `agent-${itemId}`,
            kind: "reasoning",
            title: text,
            status: "done",
          });
        }
      }
      return;
    }
    if (item?.type === "commandExecution") {
      const command = typeof item.command === "string" ? item.command : "";
      const exitCode = typeof item.exitCode === "number" ? item.exitCode : null;
      const itemId = typeof item.id === "string" ? item.id : `command-${group.commandCount}`;
      if (event.type === "item_started") {
        group.active = {
          kind: "command",
          title: "正在运行命令",
          detail: compactCommand(command),
          status: "running",
        };
        return;
      }

      group.commandCount += 1;
      if (exitCode === 0) {
        upsertStep(group, {
          id: `command-${itemId}`,
          kind: "command",
          title: commandSummaryTitle(item),
          detail: compactCommand(command),
          status: "done",
        });
        group.active = {
          kind: "reasoning",
          title: "思考中..",
          status: "running",
        };
      } else {
        group.failedCommandCount += 1;
        const failure = commandFailureSummary(item);
        upsertStep(group, {
          id: `command-${itemId}`,
          kind: "command",
          title: "命令失败，已继续处理",
          detail: combineDetails(compactCommand(command), failure),
          status: "error",
        });
        group.active = {
          kind: "error",
          title: "当前步骤失败，正在尝试其他方式",
          detail: failure ?? compactCommand(command),
          status: "error",
        };
      }
    }
    return;
  }

  if (event.type === "assistant_message") {
    const itemId = normalizeItemId(event.payload);
    if (itemId) {
      group.steps = group.steps.filter((step) => step.id !== `agent-${itemId}`);
    }
    return;
  }

  if (event.type === "turn_started") {
    group.active = { kind: "setup", title: "思考中..", status: "running" };
    return;
  }

  if (event.type === "turn_completed") {
    const turn = getPayloadObject(event.payload, "turn");
    const failed = turn?.status === "failed" || Boolean(turn?.error);
    const interrupted = turn?.status === "interrupted";
    group.status = failed ? "error" : "done";
    group.completedAt = event.created_at ?? group.createdAt;
    finishRunningSteps(group, failed ? "error" : "done");
    group.active = undefined;
    upsertStep(group, {
      id: `${group.id}-turn-completed`,
      kind: failed ? "error" : "result",
      title: interrupted ? "已停止" : failed ? "处理失败" : "处理完成",
      detail: typeof turn?.error === "string" ? turn.error : undefined,
      status: failed ? "error" : "done",
    });
    return;
  }

  if (event.type === "turn_interrupted") {
    group.status = "done";
    group.completedAt = event.created_at ?? group.createdAt;
    finishRunningSteps(group, "done");
    group.active = undefined;
    upsertStep(group, {
      id: `${group.id}-turn-interrupted`,
      kind: "result",
      title: "已停止",
      status: "done",
    });
    return;
  }

  if (event.type === "conversation_status") {
    const status = getPayloadString(event.payload, "status");
    if ((status === "queued" || status === "running") && group.status === "running") {
      group.active = {
        kind: "setup",
        title: "思考中..",
        status: "running",
      };
    }
    if (status === "stopping" && group.status === "running") {
      group.active = {
        kind: "setup",
        title: "正在停止",
        status: "running",
      };
    }
    if (status === "idle" && group.status === "running") {
      group.status = "done";
      group.completedAt = event.created_at ?? group.createdAt;
      finishRunningSteps(group, "done");
      group.active = undefined;
      upsertStep(group, {
        id: `${group.id}-turn-completed`,
        kind: "result",
        title: "处理完成",
        status: "done",
      });
    }
    if (status === "error") {
      group.status = "error";
      group.completedAt = event.created_at ?? group.createdAt;
      finishRunningSteps(group, "error");
      group.active = {
        kind: "error",
        title: "处理遇到问题",
        detail: getPayloadString(event.payload, "error"),
        status: "error",
      };
      upsertStep(group, {
        id: `${group.id}-error`,
        kind: "error",
        title: "处理失败",
        detail: getPayloadString(event.payload, "error"),
        status: "error",
      });
    }
    return;
  }

  if (event.type === "sandbox_starting") {
    group.active = { kind: "setup", title: "准备运行环境", status: "running" };
    return;
  }

  if (event.type === "sandbox_started") {
    upsertStep(group, {
      id: "runtime",
      kind: "setup",
      title: "运行环境就绪",
      status: "done",
    });
    group.active = { kind: "setup", title: "思考中..", status: "running" };
    return;
  }

  if (event.type === "skills_synced") {
    upsertStep(group, {
      id: `skills-${getPayloadString(event.payload, "hash") ?? ""}`,
      kind: "setup",
      title: `加载能力 ${getPayloadNumber(event.payload, "count") ?? ""} 个`,
      status: "done",
    });
    group.active = { kind: "setup", title: "思考中..", status: "running" };
    return;
  }

  if (event.type === "input_files_synced") {
    upsertStep(group, {
      id: `inputs-${getPayloadString(event.payload, "hash") ?? ""}`,
      kind: "file",
      title: `读取上传文件 ${getPayloadNumber(event.payload, "count") ?? ""} 个`,
      status: "done",
    });
    group.active = { kind: "file", title: "思考中..", status: "running" };
    return;
  }

  if (event.type === "artifacts_synced") {
    const files = getPayloadArray(event.payload, "files")
      .map((item) => (typeof item?.relative_path === "string" ? item.relative_path : undefined))
      .filter(Boolean)
      .slice(0, 4)
      .join(", ");
    upsertStep(group, {
      id: `history-files-${getPayloadString(event.payload, "hash") ?? ""}`,
      kind: "file",
      title: `恢复历史文件 ${getPayloadNumber(event.payload, "count") ?? 0} 个`,
      detail: files,
      status: "done",
    });
    group.active = { kind: "file", title: "历史文件已恢复", detail: files, status: "done" };
    return;
  }

  if (event.type === "app_server_initialized" || event.type === "thread_started") {
    upsertStep(group, {
      id: "runtime",
      kind: "setup",
      title: "运行环境就绪",
      status: "done",
    });
    return;
  }

  if (event.type === "artifacts_updated") {
    const files = getPayloadArray(event.payload, "artifacts")
      .map((item) => (typeof item?.relative_path === "string" ? item.relative_path : undefined))
      .filter(Boolean)
      .slice(0, 4)
      .join(", ");
    upsertStep(group, {
      id: `${group.id}-artifacts`,
      kind: "file",
      title: `保存生成文件 ${getPayloadNumber(event.payload, "count") ?? 0} 个`,
      detail: files,
      status: "done",
    });
    group.active =
      group.status === "running"
        ? { kind: "reasoning", title: "思考中..", status: "running" }
        : {
            kind: "file",
            title: "生成文件已保存",
            detail: files,
            status: "done",
          };
    return;
  }

  if (event.type === "turn_watchdog") {
    const phase = getPayloadString(event.payload, "phase");
    const message = getPayloadString(event.payload, "message") ?? "正在等待任务继续返回";
    const silenceSeconds = getPayloadNumber(event.payload, "silenceSeconds");
    group.active = {
      kind: phase === "command" ? "command" : "reasoning",
      title: message,
      detail:
        silenceSeconds && silenceSeconds >= 60
          ? `已等待 ${formatShortDuration(silenceSeconds * 1000)}`
          : undefined,
      status: "running",
    };
    return;
  }

  if (event.type === "diff_updated") {
    upsertStep(group, {
      id: `${group.id}-diff`,
      kind: "file",
      title: "更新文件变更",
      status: "done",
    });
    return;
  }

  if (event.type === "plan_updated") {
    upsertStep(group, {
      id: `${group.id}-plan`,
      kind: "reasoning",
      title: "规划处理步骤",
      status: "done",
    });
    group.active = { kind: "reasoning", title: "思考中..", status: "running" };
    return;
  }

  if (event.type === "turn_recovered") {
    group.status = "done";
    group.completedAt = event.created_at ?? group.createdAt;
    finishRunningSteps(group, "done");
    group.active = undefined;
    upsertStep(group, {
      id: `${group.id}-recovered`,
      kind: "file",
      title: "已保存生成文件",
      detail: getPayloadString(event.payload, "message"),
      status: "done",
    });
    return;
  }

  if (event.type === "error") {
    group.status = "error";
    group.completedAt = event.created_at ?? group.createdAt;
    finishRunningSteps(group, "error");
    group.active = {
      kind: "error",
      title: "处理遇到问题",
      detail: getPayloadString(event.payload, "message"),
      status: "error",
    };
    upsertStep(group, {
      id: `${group.id}-error`,
      kind: "error",
      title: "处理遇到问题",
      detail: getPayloadString(event.payload, "message"),
      status: "error",
    });
  }
}

function getTurnId(event: AgentEvent): string | undefined {
  const turn = getPayloadObject(event.payload, "turn");
  return (
    getPayloadString(event.payload, "rayueTurnId") ??
    getPayloadString(event.payload, "turnId") ??
    getPayloadString(event.payload, "turn_id") ??
    (typeof turn?.id === "string" ? turn.id : undefined)
  );
}

function shouldAttachBeforeTurn(event: AgentEvent) {
  return [
    "sandbox_starting",
    "sandbox_started",
    "skills_synced",
    "input_files_synced",
    "artifacts_synced",
    "app_server_started",
    "app_server_initialized",
    "thread_started",
  ].includes(event.type);
}

function upsertStep(group: ProcessGroup, step: ProcessStep) {
  const index = group.steps.findIndex((item) => item.id === step.id);
  if (index >= 0) {
    group.steps[index] = { ...group.steps[index], ...step };
    return;
  }
  group.steps.push(step);
}

function finishRunningSteps(group: ProcessGroup, status: "done" | "error") {
  group.steps = group.steps.map((step) =>
    step.status === "running" ? { ...step, status } : step,
  );
}

function commandSummaryTitle(item: Record<string, unknown>) {
  const durationMs = getPayloadNumber(item, "durationMs");
  if (durationMs === undefined || durationMs < 1000) {
    return "已运行命令";
  }
  return `已运行命令 ${formatShortDuration(durationMs)}`;
}

function commandFailureSummary(item: Record<string, unknown>) {
  const output =
    getPayloadString(item, "aggregatedOutput") ??
    getPayloadString(item, "stderr") ??
    getPayloadString(item, "stdout");
  if (!output) {
    return undefined;
  }
  const lines = output
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const timeout = lines.find((line) => /timed out|timeout/i.test(line));
  const exception = lines.find((line) => /exception|error|failed/i.test(line));
  const summary = timeout ?? exception ?? lines.at(-1);
  return summary ? `原因：${summary.slice(0, PROCESS_DETAIL_LIMIT)}` : undefined;
}

function combineDetails(...parts: (string | undefined)[]) {
  return parts.filter(Boolean).join("\n");
}

function formatShortDuration(durationMs: number) {
  const seconds = Math.max(1, Math.round(durationMs / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m${remainingSeconds.toString().padStart(2, "0")}s`;
}

function getPayloadNumber(payload: Record<string, unknown>, key: string): number | undefined {
  const value = payload[key];
  return typeof value === "number" ? value : undefined;
}

function getPayloadObject(payload: Record<string, unknown>, key: string): Record<string, unknown> | undefined {
  const value = payload[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function getPayloadArray(payload: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const value = payload[key];
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
}

function artifactFromPayload(item: Record<string, unknown>): Artifact | null {
  const name = typeof item.name === "string" ? item.name : undefined;
  const path = typeof item.path === "string" ? item.path : undefined;
  const relativePath =
    typeof item.relative_path === "string"
      ? item.relative_path
      : typeof item.relativePath === "string"
        ? item.relativePath
        : undefined;
  const size = typeof item.size === "number" ? item.size : undefined;
  if (!name || !path || !relativePath || size === undefined) {
    return null;
  }
  return {
    name,
    path,
    relative_path: relativePath,
    type: typeof item.type === "string" ? item.type : "file",
    size,
    modified_at: typeof item.modified_at === "string" ? item.modified_at : null,
    turn_id:
      typeof item.turn_id === "string"
        ? item.turn_id
        : typeof item.turnId === "string"
          ? item.turnId
          : null,
    first_seen_at:
      typeof item.first_seen_at === "string"
        ? item.first_seen_at
        : typeof item.firstSeenAt === "string"
          ? item.firstSeenAt
          : null,
  };
}

function eventKey(event: AgentEvent) {
  if (event.id) {
    return event.id;
  }
  return `${event.type}:${event.created_at ?? ""}:${JSON.stringify(event.payload)}`;
}

function mergeEvents(current: AgentEvent[], incoming: AgentEvent[]) {
  const byKey = new Map<string, AgentEvent>();
  current.forEach((event) => byKey.set(eventKey(event), event));
  incoming.forEach((event) => byKey.set(eventKey(event), event));
  return Array.from(byKey.values())
    .sort((left, right) => timestamp(left.created_at ?? "") - timestamp(right.created_at ?? ""))
    .slice(-MAX_EVENT_HISTORY);
}

function isPreviewableImage(artifact: Artifact) {
  return /\.(png|jpe?g|gif|webp|svg)$/i.test(artifact.relative_path);
}

function isPreviewableVideo(artifact: Artifact) {
  return /\.(mp4|mov|webm)$/i.test(artifact.relative_path);
}

function isAudioArtifact(artifact: Artifact) {
  return /\.(mp3|wav)$/i.test(artifact.relative_path);
}

function artifactTitle(artifact: Artifact) {
  return artifact.name || artifact.relative_path;
}

function selectInlineArtifacts(artifacts: Artifact[]) {
  const visible = selectPanelArtifacts(artifacts);
  const deliveryDocuments = visible.filter(isDeliveryDocumentArtifact);
  const documents = deliveryDocuments.length > 0 ? deliveryDocuments : visible.filter(isDocumentArtifact);
  const delivery =
    documents.length > 0
      ? documents
      : visible.filter((artifact) => isPrimaryArtifact(artifact) && !isAuxiliaryAssetImage(artifact));
  const selected = delivery.length > 0 ? delivery : visible;
  return selected.slice(0, INLINE_ARTIFACT_LIMIT);
}

function selectPanelArtifacts(artifacts: Artifact[]) {
  const byPath = new Map(artifacts.map((artifact) => [artifact.relative_path, artifact]));
  return artifacts
    .filter((artifact) => !isPreviewDerivative(artifact, byPath))
    .sort(compareArtifacts);
}

function isPrimaryArtifact(artifact: Artifact) {
  return /\.(pptx|pdf|docx|xlsx|csv|zip|html|png|jpe?g|gif|webp|svg|mp4|mov|webm|mp3|wav)$/i.test(
    artifact.relative_path,
  );
}

function isDocumentArtifact(artifact: Artifact) {
  return /\.(pptx|pdf|docx|xlsx|csv|zip|html)$/i.test(artifact.relative_path);
}

function isDeliveryDocumentArtifact(artifact: Artifact) {
  return /\.(pptx|pdf|docx|xlsx|csv|zip)$/i.test(artifact.relative_path);
}

function isAuxiliaryAssetImage(artifact: Artifact) {
  return /(^|\/)[^/]*assets\//i.test(artifact.relative_path) && isPreviewableImage(artifact);
}

function isPreviewDerivative(artifact: Artifact, byPath: Map<string, Artifact>) {
  return originalCandidatesForPreview(artifact.relative_path).some((path) => byPath.has(path));
}

function originalCandidatesForPreview(path: string) {
  const match = path.match(/^(.*)_small\.(?:jpe?g|png|webp)$/i);
  if (!match) {
    return [];
  }
  const base = match[1];
  return [".png", ".jpg", ".jpeg", ".webp"].map((extension) => `${base}${extension}`);
}

function buildPreviewPathMap(artifacts: Artifact[]) {
  const paths = new Set(artifacts.map((artifact) => artifact.relative_path));
  const previewPaths: Record<string, string> = {};
  artifacts.forEach((artifact) => {
    if (!isPreviewableImage(artifact)) {
      return;
    }
    const dotIndex = artifact.relative_path.lastIndexOf(".");
    if (dotIndex < 0) {
      return;
    }
    const base = artifact.relative_path.slice(0, dotIndex);
    const candidates = [`${base}_small.jpg`, `${base}_small.jpeg`, `${base}_small.png`, `${base}_small.webp`];
    const preview = candidates.find((candidate) => paths.has(candidate));
    if (preview) {
      previewPaths[artifact.path] = preview;
    }
  });
  return previewPaths;
}

function artifactRank(artifact: Artifact) {
  const path = artifact.relative_path.toLowerCase();
  if (path.endsWith(".pptx")) return 0;
  if (path.endsWith(".pdf")) return 1;
  if (/\.(docx|xlsx|csv|zip|html)$/.test(path)) return 2;
  if (/\.(png|jpe?g|gif|webp|svg)$/.test(path)) return 3;
  if (/\.(mp4|mov|webm|mp3|wav)$/.test(path)) return 4;
  return 5;
}

function compareArtifacts(left: Artifact, right: Artifact) {
  const rank = artifactRank(left) - artifactRank(right);
  if (rank !== 0) {
    return rank;
  }
  return left.relative_path.localeCompare(right.relative_path);
}

function compactCommand(command: string) {
  const stripped = sanitizeDisplayCommand(
    command.replace(/^\/bin\/bash -lc\s+/, "").replace(/\s+/g, " ").trim(),
  );
  return stripped.length > PROCESS_DETAIL_LIMIT
    ? `${stripped.slice(0, PROCESS_DETAIL_LIMIT - 1)}...`
    : stripped;
}

function sanitizeDisplayCommand(value: string) {
  return value
    .replace(/sk-[A-Za-z0-9_-]{8,}/g, "[密钥]")
    .replace(/\b(?:OPENAI|GPT_IMAGE2|CODEX)_[A-Z0-9_]*=[^\s]+/g, "[环境变量]")
    .replace(/\/home\/[^/\s]+\/\.codex\/[^\s'"`]+/gi, "[工具目录]")
    .replace(/\/tmp\/codex[^\s'"`]+/gi, "[临时文件]")
    .replace(/\bcodex\b/gi, "工具");
}

function timestamp(value: string) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function formatProcessDuration(start: string, end?: string) {
  const startedAt = timestamp(start);
  const endedAt = end ? timestamp(end) : Date.now();
  const seconds = Math.max(1, Math.round((endedAt - startedAt) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes === 0) {
    return `${remainingSeconds}s`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours === 0) {
    return `${minutes}m${remainingSeconds.toString().padStart(2, "0")}s`;
  }
  return `${hours}h${remainingMinutes.toString().padStart(2, "0")}m`;
}

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [turns, setTurns] = useState<AgentTurn[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [uploads, setUploads] = useState<UploadedFile[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillName, setSkillName] = useState("team-defaults");
  const [skillContent, setSkillContent] = useState(
    "---\nname: team-defaults\ndescription: Shared behavior for all demo users.\n---\n\n# Team Defaults\n\nAnswer in Chinese unless the user asks otherwise. Prefer root-cause analysis before implementation.\n",
  );
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const activeIdRef = useRef<string | null>(null);
  const activeStatusRef = useRef("idle");
  const stickToBottomRef = useRef(false);
  const forceScrollBottomRef = useRef(false);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId) ?? null,
    [activeId, conversations],
  );
  const activeStatus = activeConversation?.status ?? "idle";
  const turnBusy = activeStatus === "queued" || activeStatus === "running" || activeStatus === "stopping";
  const stopInProgress = stopping || activeStatus === "stopping";

  useEffect(() => {
    activeStatusRef.current = activeStatus;
    if (activeStatus !== "stopping") {
      setStopping(false);
    }
  }, [activeStatus]);
  const processGroups = useMemo(() => buildProcessGroups(events), [events]);
  const artifactGroups = useMemo(
    () => buildArtifactGroups(artifacts, events, turns, processGroups),
    [artifacts, events, turns, processGroups],
  );
  const timelineItems = useMemo(
    () => buildTimeline(messages, processGroups, artifactGroups, turns),
    [messages, processGroups, artifactGroups, turns],
  );

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  const isActiveConversation = useCallback((conversationId: string) => {
    return activeIdRef.current === conversationId;
  }, []);

  const switchConversation = useCallback((conversationId: string | null) => {
    activeIdRef.current = conversationId;
    activeStatusRef.current =
      conversations.find((conversation) => conversation.id === conversationId)?.status ?? "idle";
    setActiveId(conversationId);
    setMessages([]);
    setEvents([]);
    setTurns([]);
    setArtifacts([]);
    setUploads([]);
    setError(null);
    setStopping(false);
    stickToBottomRef.current = false;
    forceScrollBottomRef.current = false;
  }, [conversations]);

  const refreshConversations = useCallback(async () => {
    const rows = await listConversations();
    setConversations(rows);
    if (!activeId && rows.length > 0) {
      switchConversation(rows[0].id);
    }
  }, [activeId, switchConversation]);

  const updateScrollStickiness = useCallback(() => {
    const element = scrollContainerRef.current;
    if (!element) {
      return;
    }
    const distanceToBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    stickToBottomRef.current = distanceToBottom < 120;
  }, []);

  const loadActive = useCallback(async (conversationId: string, mode: "replace" | "merge" = "merge") => {
    const [detail, artifactRows, uploadRows] = await Promise.all([
      getConversation(conversationId),
      listArtifacts(conversationId).catch(() => [] as Artifact[]),
      listUploads(conversationId).catch(() => [] as UploadedFile[]),
    ]);
    if (!isActiveConversation(conversationId)) {
      return;
    }
    const apiMessages = detail.messages.map(messageFromApi);
    setMessages((prev) => (mode === "replace" ? apiMessages : mergeMessages(prev, apiMessages)));
    setEvents((prev) => (mode === "replace" ? detail.events : mergeEvents(prev, detail.events)));
    setTurns(detail.turns);
    setArtifacts(artifactRows);
    setUploads(uploadRows);
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === detail.conversation.id ? detail.conversation : conversation,
      ),
    );
  }, [isActiveConversation]);

  useEffect(() => {
    async function boot() {
      try {
        setLoading(true);
        const [rows, skillRows] = await Promise.all([listConversations(), listSkills()]);
        if (rows.length === 0) {
          const created = await createConversation();
          setConversations([created]);
          switchConversation(created.id);
        } else {
          setConversations(rows);
          switchConversation(rows[0].id);
        }
        setSkills(skillRows);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load app");
      } finally {
        setLoading(false);
      }
    }
    void boot();
  }, []);

  useEffect(() => {
    if (!activeId) {
      return;
    }
    const conversationId = activeId;
    void loadActive(conversationId, "replace").catch((err) => {
      if (isActiveConversation(conversationId)) {
        setError(err instanceof Error ? err.message : "Failed to load conversation");
      }
    });
    const source = new EventSource(`${getApiBase()}/api/conversations/${conversationId}/events`);
    let reconnectRefresh: ReturnType<typeof setTimeout> | null = null;
    const refreshActive = () => {
      void loadActive(conversationId).catch(() => undefined);
    };
    const fallbackPoll = window.setInterval(() => {
      if (
        activeStatusRef.current === "running" ||
        activeStatusRef.current === "queued" ||
        activeStatusRef.current === "stopping"
      ) {
        refreshActive();
      }
    }, 3000);
    source.onopen = () => {
      refreshActive();
    };
    source.onmessage = (messageEvent) => {
      const event = JSON.parse(messageEvent.data) as AgentEvent;
      if (!isActiveConversation(conversationId)) {
        return;
      }
      if (event.conversation_id && event.conversation_id !== conversationId) {
        return;
      }
      setEvents((prev) => mergeEvents(prev, [event]));
      if (event.type === "conversation_status") {
        const status = getPayloadString(event.payload, "status");
        const eventError = getPayloadString(event.payload, "error") ?? null;
        if (status) {
          activeStatusRef.current = status;
          if (status !== "stopping") {
            setStopping(false);
          }
          setConversations((prev) =>
            prev.map((conversation) =>
              conversation.id === conversationId
                ? { ...conversation, status, error: eventError }
                : conversation,
            ),
          );
        }
      }
      if (event.type === "assistant_delta") {
        return;
      }
      if (event.type === "assistant_message") {
        const itemId = normalizeItemId(event.payload) ?? getPayloadString(event.payload, "item_id");
        const text = getPayloadString(event.payload, "text") ?? "";
        if (itemId && text) {
          setMessages((prev) => {
            const existing = prev.find((msg) => msg.role === "assistant" && msg.itemId === itemId);
            if (existing) {
              return prev.map((msg) =>
                msg.id === existing.id ? { ...msg, content: text, pending: false } : msg,
              );
            }
            return [
              ...prev,
              {
                id: `assistant-${itemId}`,
                role: "assistant",
                content: text,
                itemId,
                createdAt: event.created_at ?? new Date().toISOString(),
                pending: false,
              },
            ];
          });
        }
      }
      if (event.type === "artifacts_updated" || event.type === "turn_completed") {
        void listArtifacts(conversationId)
          .then((rows) => {
            if (isActiveConversation(conversationId)) {
              setArtifacts(rows);
            }
          })
          .catch(() => undefined);
      }
      if (event.type === "uploads_updated" || event.type === "input_files_synced") {
        void listUploads(conversationId)
          .then((rows) => {
            if (isActiveConversation(conversationId)) {
              setUploads(rows);
            }
          })
          .catch(() => undefined);
      }
    };
    source.onerror = () => {
      if (reconnectRefresh) {
        clearTimeout(reconnectRefresh);
      }
      reconnectRefresh = setTimeout(refreshActive, 800);
    };
    return () => {
      source.close();
      window.clearInterval(fallbackPoll);
      if (reconnectRefresh) {
        clearTimeout(reconnectRefresh);
      }
    };
  }, [activeId, isActiveConversation, loadActive]);

  useEffect(() => {
    if (!forceScrollBottomRef.current && !stickToBottomRef.current) {
      return;
    }
    const shouldAnimate = forceScrollBottomRef.current;
    forceScrollBottomRef.current = false;
    window.requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({
        behavior: shouldAnimate ? "smooth" : "auto",
        block: "end",
      });
      stickToBottomRef.current = true;
    });
  }, [timelineItems]);

  async function handleNewConversation() {
    try {
      const conversation = await createConversation();
      setConversations((prev) => [conversation, ...prev]);
      switchConversation(conversation.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create conversation");
    }
  }

  async function handleDeleteConversation(conversationId: string) {
    const conversation = conversations.find((item) => item.id === conversationId);
    const confirmed = window.confirm(
      `删除会话"${conversation?.title ?? "未命名会话"}"？\n\n会同时删除这条会话的生成文件和上传资料。`,
    );
    if (!confirmed) {
      return;
    }
    try {
      await deleteConversation(conversationId);
      let next = conversations.filter((item) => item.id !== conversationId);
      if (next.length === 0) {
        const created = await createConversation();
        next = [created];
      }
      setConversations(next);
      if (activeId === conversationId) {
        switchConversation(next[0]?.id ?? null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete conversation");
    }
  }

  async function handleSend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeId || !input.trim() || sending || turnBusy) {
      return;
    }
    const content = input.trim();
    const now = new Date().toISOString();
    const localMessageId = `local-user-${Date.now()}`;
    const localStatusEventId = `local-status-${localMessageId}`;
    setInput("");
    setSending(true);
    setError(null);
    activeStatusRef.current = "queued";
    stickToBottomRef.current = true;
    forceScrollBottomRef.current = true;
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === activeId
          ? { ...conversation, status: "queued", error: null, updated_at: now, last_activity_at: now }
          : conversation,
      ),
    );
    setMessages((prev) => [
      ...prev,
      { id: localMessageId, role: "user", content, createdAt: now },
    ]);
    setEvents((prev) =>
      mergeEvents(prev, [
        {
          id: localStatusEventId,
          conversation_id: activeId,
          type: "conversation_status",
          payload: { status: "queued", error: null },
          created_at: now,
        },
      ]),
    );
    try {
      const response = await sendMessage(activeId, content);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === localMessageId
            ? {
                ...message,
                id: response.message.id,
                itemId: response.message.item_id,
                createdAt: response.message.created_at,
              }
            : message,
        ),
      );
      await refreshConversations();
    } catch (err) {
      setEvents((prev) => prev.filter((eventItem) => eventItem.id !== localStatusEventId));
      activeStatusRef.current = "idle";
      setConversations((prev) =>
        prev.map((conversation) =>
          conversation.id === activeId ? { ...conversation, status: "idle" } : conversation,
        ),
      );
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setSending(false);
    }
  }

  async function handleStop() {
    if (!activeId || sending || stopping || !turnBusy) {
      return;
    }
    const now = new Date().toISOString();
    setStopping(true);
    setError(null);
    activeStatusRef.current = "stopping";
    setConversations((prev) =>
      prev.map((conversation) =>
        conversation.id === activeId
          ? { ...conversation, status: "stopping", error: null, updated_at: now }
          : conversation,
      ),
    );
    setEvents((prev) =>
      mergeEvents(prev, [
        {
          id: `local-stop-${Date.now()}`,
          conversation_id: activeId,
          type: "conversation_status",
          payload: { status: "stopping", error: null },
          created_at: now,
        },
      ]),
    );
    try {
      await stopConversation(activeId);
      await Promise.all([loadActive(activeId), refreshConversations()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop task");
      void loadActive(activeId).catch(() => undefined);
    } finally {
      setStopping(false);
    }
  }

  async function handleSaveSkill() {
    try {
      const skill = await putSkill(skillName.trim(), skillContent);
      setSkills((prev) => [skill, ...prev.filter((item) => item.name !== skill.name)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save skill");
    }
  }

  async function handleUploadFiles(fileList: FileList | null) {
    if (!activeId || !fileList || fileList.length === 0 || uploading) {
      return;
    }
    const conversationId = activeId;
    const selected = Array.from(fileList);
    if (selected.length > MAX_UPLOAD_FILES) {
      setError(`一次最多上传 ${MAX_UPLOAD_FILES} 个文件`);
      return;
    }
    const oversized = selected.find((file) => file.size > MAX_UPLOAD_BYTES);
    if (oversized) {
      setError(`${oversized.name} 超过 50MB 单文件限制`);
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const rows = await uploadFiles(conversationId, selected);
      if (isActiveConversation(conversationId)) {
        setUploads(rows);
      }
    } catch (err) {
      if (isActiveConversation(conversationId)) {
        setError(err instanceof Error ? err.message : "Failed to upload files");
      }
    } finally {
      setUploading(false);
      if (uploadInputRef.current) {
        uploadInputRef.current.value = "";
      }
    }
  }

  return (
    <main className="flex h-screen min-h-0 bg-[#f2f4f7] text-ink">
      <aside className="flex w-[292px] shrink-0 flex-col border-r border-line bg-white">
        <div className="flex h-16 items-center justify-between border-b border-line px-4">
          <div className="flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-md bg-ink text-white">
              <Code2 size={18} />
            </div>
            <div>
              <div className="text-sm font-semibold">Rayue</div>
              <div className="text-xs text-muted">智能体工作台</div>
            </div>
          </div>
          <button
            className="flex h-9 w-9 items-center justify-center rounded-md border border-line hover:bg-panel"
            onClick={handleNewConversation}
            title="新对话"
          >
            <Plus size={17} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {loading ? (
            <div className="flex items-center gap-2 px-2 py-3 text-sm text-muted">
              <Loader2 className="animate-spin" size={16} />
              加载中
            </div>
          ) : (
            conversations.map((conversation) => (
              <div
                key={conversation.id}
                className={clsx(
                  "group mb-2 rounded-md border transition",
                  conversation.id === activeId
                    ? "border-ink bg-ink text-white"
                    : "border-line bg-white hover:bg-panel",
                )}
              >
                <button
                  onClick={() => switchConversation(conversation.id)}
                  className="w-full px-3 py-3 text-left"
                >
                  <div className="line-clamp-2 text-sm font-medium">{conversation.title}</div>
                  <div
                    className={clsx(
                      "mt-2 flex items-center justify-between text-xs",
                      conversation.id === activeId ? "text-white/70" : "text-muted",
                    )}
                  >
                    <span>{statusLabel[conversation.status] ?? conversation.status}</span>
                    <MessageSquarePlus size={14} />
                  </div>
                </button>
                <div
                  className={clsx(
                    "flex justify-end border-t px-2 py-1",
                    conversation.id === activeId ? "border-white/10" : "border-line",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => void handleDeleteConversation(conversation.id)}
                    className={clsx(
                      "flex h-7 w-7 items-center justify-center rounded opacity-70 hover:bg-bad/10 hover:text-bad group-hover:opacity-100",
                      conversation.id === activeId ? "text-white/80" : "text-muted",
                    )}
                    title="删除会话"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
        <AdminSkillsPanel
          skills={skills}
          skillName={skillName}
          skillContent={skillContent}
          onNameChange={setSkillName}
          onContentChange={setSkillContent}
          onSave={handleSaveSkill}
        />
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-line bg-white px-5">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">
              {activeConversation?.title ?? "暂无会话"}
            </div>
          </div>
          <button
            onClick={() => activeId && loadActive(activeId)}
            className="flex h-9 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm hover:bg-panel"
          >
            <RefreshCw size={15} />
            刷新
          </button>
        </header>

        {error ? (
          <div className="border-b border-bad/20 bg-red-50 px-5 py-3 text-sm text-bad">{error}</div>
        ) : null}

        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-h-0 flex-col">
            <div
              ref={scrollContainerRef}
              onScroll={updateScrollStickiness}
              className="min-h-0 flex-1 overflow-y-auto px-6 py-5"
            >
              {messages.length === 0 ? (
                <div className="flex h-full items-center justify-center">
                  <div className="max-w-md text-center">
                    <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-md bg-white shadow-soft">
                      <Sparkles size={22} />
                    </div>
                    <h1 className="text-lg font-semibold">开始使用 Rayue</h1>
                    <p className="mt-2 text-sm leading-6 text-muted">
                      输入任务、上传资料，Rayue 会在对话中展示过程、结果和生成文件。
                    </p>
                  </div>
                </div>
              ) : (
                <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
                  {timelineItems.map((item) =>
                    item.type === "message" ? (
                      <MessageBubble key={`message-${item.message.id}`} message={item.message} />
                    ) : item.type === "process" ? (
                      <ProcessCard key={`process-${item.group.id}`} group={item.group} />
                    ) : (
                      <ArtifactMessage
                        key={`artifacts-${item.group.id}`}
                        conversationId={activeId}
                        group={item.group}
                      />
                    ),
                  )}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>
            <form onSubmit={handleSend} className="border-t border-line bg-white p-4">
              <div className="mx-auto flex max-w-4xl gap-3">
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  rows={2}
                  className="min-h-[52px] flex-1 resize-none rounded-md border-line text-sm shadow-sm focus:border-accent focus:ring-accent"
                  placeholder="输入任务，例如：在工作区写一个 README 并解释设计取舍"
                />
                <button
                  type={turnBusy ? "button" : "submit"}
                  onClick={turnBusy ? handleStop : undefined}
                  disabled={
                    !activeId ||
                    (turnBusy ? sending || stopInProgress : !input.trim() || sending)
                  }
                  className={clsx(
                    "flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-md text-white disabled:cursor-not-allowed disabled:bg-muted",
                    turnBusy ? "bg-bad hover:bg-bad/90" : "bg-ink hover:bg-ink/90",
                  )}
                  title={turnBusy ? (stopInProgress ? "正在停止" : "停止当前任务") : "发送"}
                >
                  {turnBusy ? (
                    stopInProgress ? (
                      <Loader2 className="animate-spin" size={18} />
                    ) : (
                      <Square size={18} fill="currentColor" />
                    )
                  ) : sending ? (
                    <Loader2 className="animate-spin" size={18} />
                  ) : (
                    <Send size={18} />
                  )}
                </button>
              </div>
            </form>
          </div>
          <WorkspacePanel
            conversation={activeConversation}
            uploads={uploads}
            artifacts={artifacts}
            conversationId={activeId}
            uploading={uploading}
            onPickFiles={() => uploadInputRef.current?.click()}
            onRefreshArtifacts={() => {
              if (activeId) {
                const conversationId = activeId;
                void listArtifacts(conversationId)
                  .then((rows) => {
                    if (isActiveConversation(conversationId)) {
                      setArtifacts(rows);
                    }
                  })
                  .catch(() => {
                    if (isActiveConversation(conversationId)) {
                      setArtifacts([]);
                    }
                  });
              }
            }}
          />
          <input
            ref={uploadInputRef}
            className="hidden"
            type="file"
            multiple
            onChange={(event) => void handleUploadFiles(event.target.files)}
          />
        </div>
      </section>
    </main>
  );
}

function MessageBubble({ message }: { message: LocalMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={clsx("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={clsx(
          "max-w-[78%] text-sm leading-7",
          isUser
            ? "rounded-md bg-ink px-4 py-3 text-white shadow-sm"
            : "px-1 py-1 text-[15px] text-ink",
        )}
      >
        <MarkdownContent content={message.content} inverted={isUser} />
        {message.pending ? <span className="ml-1 inline-block h-2 w-2 animate-pulse rounded-full bg-accent" /> : null}
      </div>
    </div>
  );
}

function MarkdownContent({ content, inverted = false }: { content: string; inverted?: boolean }) {
  return (
    <div className={clsx("markdown-content break-words", inverted && "markdown-inverted")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-3 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
          ol: ({ children }) => <ol className="mb-3 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
          li: ({ children }) => <li className="pl-1">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          a: ({ children, href }) => (
            <a
              className={clsx("underline underline-offset-2", inverted ? "text-white" : "text-accent")}
              href={href}
              target="_blank"
              rel="noreferrer"
            >
              {children}
            </a>
          ),
          code: ({ children, className }) => {
            const codeText = String(children);
            const isBlock = Boolean(className) || codeText.includes("\n");
            if (isBlock) {
              return (
                <code className={clsx("block overflow-x-auto rounded-md px-3 py-2 font-mono text-xs leading-5", inverted ? "bg-white/10 text-white" : "bg-white text-ink")}>
                  {children}
                </code>
              );
            }
            return (
              <code className={clsx("rounded px-1.5 py-0.5 font-mono text-[0.92em]", inverted ? "bg-white/10 text-white" : "bg-panel text-ink")}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => <pre className="mb-3 overflow-x-auto last:mb-0">{children}</pre>,
          blockquote: ({ children }) => (
            <blockquote className={clsx("mb-3 border-l-2 pl-3 last:mb-0", inverted ? "border-white/40 text-white/85" : "border-line text-muted")}>
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="mb-3 overflow-x-auto last:mb-0">
              <table className="min-w-full border-collapse text-left text-xs">{children}</table>
            </div>
          ),
          th: ({ children }) => <th className={clsx("border px-2 py-1 font-semibold", inverted ? "border-white/20" : "border-line")}>{children}</th>,
          td: ({ children }) => <td className={clsx("border px-2 py-1", inverted ? "border-white/20" : "border-line")}>{children}</td>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function liveProcessContext(active?: ActiveProcess) {
  if (!active || active.status === "done" || active.title === "思考中..") {
    return null;
  }
  return active.title;
}

function liveProcessDetail(active?: ActiveProcess) {
  if (!active || active.status === "done") {
    return null;
  }
  return active.detail ?? null;
}

function visibleProcessSteps(group: ProcessGroup) {
  return group.steps
    .filter((step) => step.kind !== "result" || group.status !== "running")
    .slice(group.status === "running" ? -6 : -10);
}

function ProcessCard({ group }: { group: ProcessGroup }) {
  const [expanded, setExpanded] = useState(group.status !== "done");
  const [, setClockTick] = useState(0);
  const previousStatusRef = useRef(group.status);
  useEffect(() => {
    if (previousStatusRef.current === group.status) {
      return;
    }
    previousStatusRef.current = group.status;
    setExpanded(group.status !== "done");
  }, [group.status]);
  useEffect(() => {
    if (group.status !== "running") {
      return;
    }
    const timer = window.setInterval(() => setClockTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [group.status]);

  const duration = formatProcessDuration(group.createdAt, group.completedAt);
  const active = group.active;
  const liveContext = liveProcessContext(active);
  const liveDetail = liveProcessDetail(active);
  const visibleSteps = visibleProcessSteps(group);
  const showStepLog = visibleSteps.length > 0 && (group.status === "running" || expanded);
  return (
    <div className={clsx("flex justify-start", group.status === "done" && !expanded && "-mb-3")}>
      <div
        className={clsx(
          "w-full max-w-[78%] px-1 text-sm text-muted",
          group.status === "running" && "text-muted",
          group.status === "error" && "text-bad",
        )}
      >
        <button
          type="button"
          className={clsx(
            "flex w-full items-center justify-between gap-3 text-left",
            group.status === "done" ? "cursor-pointer" : "cursor-default",
          )}
          onClick={() => group.status === "done" && setExpanded((value) => !value)}
          aria-expanded={group.status === "done" ? expanded : true}
          aria-disabled={group.status !== "done"}
          title={group.status === "done" ? "展开或折叠执行过程" : undefined}
        >
          <div className="flex min-w-0 items-center gap-2 text-sm">
            {group.status === "running" ? (
              <Loader2 className="shrink-0 animate-spin text-muted" size={15} />
            ) : group.status === "error" ? (
              <XCircle className="shrink-0 text-bad" size={15} />
            ) : (
              <Lightbulb className="shrink-0 text-muted" size={15} />
            )}
            <span className="min-w-0 truncate">
              {group.status === "running" ? "思考中.." : group.status === "done" ? `思考了 ${duration}` : active?.title ?? "处理遇到问题"}
            </span>
            {group.status === "running" ? <span className="shrink-0 text-xs text-muted/80">({duration})</span> : null}
            {liveContext ? <span className="min-w-0 truncate text-xs text-muted/80">· {liveContext}</span> : null}
          </div>
          <span className="flex shrink-0 items-center gap-1 text-xs text-muted">
            {group.status === "running" ? null : group.status === "error" ? "查看" : expanded ? "收起" : "展开"}
            {group.status === "done" ? (
              expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />
            ) : null}
          </span>
        </button>
        {showStepLog ? (
          <div className="mt-3 space-y-3 border-l border-line/70 pl-4 text-muted">
            {visibleSteps.map((step) => (
              <div key={step.id} className="flex gap-2 text-sm leading-6">
                <span className="mt-0.5 shrink-0 text-muted">{processIcon(step)}</span>
                <div className="min-w-0 flex-1">
                  <div className={clsx("font-medium", step.kind === "reasoning" && "text-ink/80")}>
                    {step.kind === "reasoning" ? <MarkdownContent content={step.title} /> : step.title}
                  </div>
                  {step.detail ? (
                    <div className="mt-0.5 truncate font-mono text-[11px] text-muted/80">{step.detail}</div>
                  ) : null}
                </div>
              </div>
            ))}
            {liveDetail ? (
              <div className="flex gap-2 text-sm leading-6">
                <span className="mt-0.5 shrink-0 text-muted">{processIconFor(active?.kind ?? "command", "running")}</span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{liveContext ?? "正在处理"}</div>
                  <div className="mt-0.5 truncate font-mono text-[11px] text-muted/80">{liveDetail}</div>
                </div>
              </div>
            ) : null}
          </div>
        ) : liveDetail ? (
          <div className="mt-1 flex min-w-0 items-start gap-2 pl-6 text-xs leading-5 text-muted/75">
            <span className="shrink-0">└</span>
            <div className="min-w-0 flex-1 truncate font-mono text-[11px]">{liveDetail}</div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ArtifactMessage({
  conversationId,
  group,
}: {
  conversationId: string | null;
  group: ArtifactGroup;
}) {
  if (!conversationId) {
    return null;
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[78%] px-1">
        <div className="mb-2 flex items-center gap-2 text-xs text-muted">
          <FileText size={14} />
          <span>结果文件</span>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {group.files.map((artifact) => (
            <a
              key={artifact.path}
              className="group overflow-hidden rounded-md border border-line bg-white text-xs text-ink shadow-sm hover:border-ink"
              href={artifactDownloadUrl(conversationId, artifact.path)}
              download={artifact.name}
            >
              {isPreviewableImage(artifact) ? (
                <img
                  src={artifactDownloadUrl(conversationId, group.previewPaths[artifact.path] ?? artifact.path)}
                  alt={artifactTitle(artifact)}
                  className="h-24 w-full object-cover"
                />
              ) : isPreviewableVideo(artifact) ? (
                <video
                  src={artifactDownloadUrl(conversationId, artifact.path)}
                  className="h-24 w-full bg-black object-contain"
                  controls
                  preload="metadata"
                />
              ) : null}
              <div className="flex min-w-0 items-center gap-2 px-3 py-2">
                <Download size={14} className="shrink-0 text-muted group-hover:text-ink" />
                <span className="min-w-0 flex-1 truncate">{artifact.relative_path}</span>
                <span className="shrink-0 text-muted">{formatBytes(artifact.size)}</span>
              </div>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function processIcon(step: ProcessStep) {
  return processIconFor(step.kind, step.status);
}

function processIconFor(
  kind: ProcessStep["kind"] | ActiveProcess["kind"],
  status?: ProcessStep["status"],
) {
  if (status === "running") {
    return <Loader2 className="animate-spin text-muted" size={14} />;
  }
  if (status === "error" || kind === "error") {
    return <XCircle className="text-bad" size={14} />;
  }
  if (kind === "command") {
    return <SquareTerminal size={14} />;
  }
  if (kind === "file") {
    return <FileText size={14} />;
  }
  if (kind === "reasoning" || kind === "network") {
    return <Wrench size={14} />;
  }
  return <CheckCircle2 className="text-good" size={14} />;
}

function WorkspacePanel({
  conversation,
  uploads,
  artifacts,
  conversationId,
  uploading,
  onPickFiles,
  onRefreshArtifacts,
}: {
  conversation: Conversation | null;
  uploads: UploadedFile[];
  artifacts: Artifact[];
  conversationId: string | null;
  uploading: boolean;
  onPickFiles: () => void;
  onRefreshArtifacts: () => void;
}) {
  return (
    <aside className="min-h-0 border-l border-line bg-white">
      <div className="flex h-16 items-center justify-between border-b border-line px-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Paperclip size={17} />
          工作区
        </div>
        <StatusBadge status={conversation?.status ?? "idle"} />
      </div>
      <div className="min-h-0 h-[calc(100vh-4rem)] overflow-y-auto p-3">
        {conversation?.error ? (
          <div className="mb-3 rounded-md border border-bad/20 bg-red-50 p-3 text-sm text-bad">
            {conversation.error}
          </div>
        ) : null}
        <InputFilesPanel
          uploads={uploads}
          conversationId={conversationId}
          uploading={uploading}
          onPickFiles={onPickFiles}
        />
        <ArtifactPanel
          artifacts={artifacts}
          conversationId={conversationId}
          onRefresh={onRefreshArtifacts}
        />
      </div>
    </aside>
  );
}

function ArtifactPanel({
  artifacts,
  conversationId,
  onRefresh,
}: {
  artifacts: Artifact[];
  conversationId: string | null;
  onRefresh: () => void;
}) {
  const displayedArtifacts = selectPanelArtifacts(artifacts);
  return (
    <section className="mb-3 rounded-md border border-line bg-white">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <FileText size={15} />
          生成文件
        </div>
        <button
          className="flex h-7 items-center gap-1 rounded border border-line px-2 text-xs hover:bg-panel"
          onClick={onRefresh}
          disabled={!conversationId}
        >
          <RefreshCw size={13} />
          刷新
        </button>
      </div>
      <div className="max-h-48 overflow-y-auto p-2">
        {displayedArtifacts.length === 0 ? (
          <div className="px-1 py-2 text-xs text-muted">暂无可下载文件</div>
        ) : (
          displayedArtifacts.map((artifact) => (
            <a
              key={artifact.path}
              className="mb-1 flex items-center gap-2 rounded-md px-2 py-2 text-xs hover:bg-panel"
              href={conversationId ? artifactDownloadUrl(conversationId, artifact.path) : "#"}
              download={artifact.name}
            >
              <Download size={14} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate">{artifact.relative_path}</span>
              <span className="shrink-0 text-muted">{formatBytes(artifact.size)}</span>
            </a>
          ))
        )}
      </div>
    </section>
  );
}

function InputFilesPanel({
  uploads,
  conversationId,
  uploading,
  onPickFiles,
}: {
  uploads: UploadedFile[];
  conversationId: string | null;
  uploading: boolean;
  onPickFiles: () => void;
}) {
  return (
    <section className="mb-3 rounded-md border border-line bg-white">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="flex items-center gap-2 text-xs font-semibold">
          <Paperclip size={15} />
          上传资料
        </div>
        <button
          className="flex h-7 items-center gap-1 rounded border border-line px-2 text-xs hover:bg-panel disabled:cursor-not-allowed disabled:text-muted"
          onClick={onPickFiles}
          disabled={!conversationId || uploading}
        >
          {uploading ? <Loader2 className="animate-spin" size={13} /> : <FileUp size={13} />}
          上传
        </button>
      </div>
      <div className="max-h-48 overflow-y-auto p-2">
        {uploads.length === 0 ? (
          <div className="px-1 py-2 text-xs text-muted">暂无上传资料</div>
        ) : (
          uploads.map((file) => (
            <div key={file.relative_path} className="mb-1 flex items-center gap-2 rounded-md px-2 py-2 text-xs">
              <FileText size={14} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate">{file.relative_path}</span>
              <span className="shrink-0 text-muted">{formatBytes(file.size)}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function StatusBadge({ status }: { status: string }) {
  return (
    <div
      className={clsx(
        "rounded-md px-2 py-1 text-xs font-medium",
        status === "running" && "bg-blue-50 text-accent",
        status === "queued" && "bg-amber-50 text-warn",
        status === "stopping" && "bg-red-50 text-bad",
        status === "error" && "bg-red-50 text-bad",
        status === "idle" && "bg-emerald-50 text-good",
      )}
    >
      {statusLabel[status] ?? status}
    </div>
  );
}

function AdminSkillsPanel({
  skills,
  skillName,
  skillContent,
  onNameChange,
  onContentChange,
  onSave,
}: {
  skills: Skill[];
  skillName: string;
  skillContent: string;
  onNameChange: (name: string) => void;
  onContentChange: (content: string) => void;
  onSave: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-line bg-white p-3">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between rounded-md border border-line px-3 py-2 text-sm hover:bg-panel"
      >
        <span className="flex items-center gap-2">
          <Settings size={15} />
          Admin skills
        </span>
        <span className="text-xs text-muted">{skills.length}</span>
      </button>
      {open ? (
        <div className="mt-3 space-y-3">
          <div className="space-y-1">
            {skills.map((skill) => (
              <div key={skill.name} className="rounded-md bg-panel px-2 py-1 text-xs text-muted">
                {skill.name}
              </div>
            ))}
          </div>
          <input
            value={skillName}
            onChange={(event) => onNameChange(event.target.value)}
            className="w-full rounded-md border-line text-xs focus:border-accent focus:ring-accent"
          />
          <textarea
            value={skillContent}
            onChange={(event) => onContentChange(event.target.value)}
            rows={7}
            className="w-full resize-none rounded-md border-line font-mono text-[11px] leading-5 focus:border-accent focus:ring-accent"
          />
          <button
            onClick={onSave}
            className="flex h-9 w-full items-center justify-center gap-2 rounded-md bg-ink text-sm text-white"
          >
            <CheckCircle2 size={15} />
            Save skill
          </button>
        </div>
      ) : null}
    </div>
  );
}
