export type { paths, components, operations } from "./openapi";
import type { components } from "./openapi";

type S = components["schemas"];

export type User = S["UserOut"];
export type TokenOut = S["TokenOut"];
export type Org = S["OrgWithRole"];
export type Member = S["MemberOut"];
export type Invite = S["InviteOut"];
export type Project = S["ProjectWithRole"];
export type FileInfo = S["FileOut"];
export type Job = S["JobOut"];
export type Artifact = S["ArtifactOut"];
export type ArtifactVersion = S["ArtifactVersionOut"];
export type Provider = S["ProviderOut"];
export type ProviderKind = S["ProviderKindOut"];
export type ModelConfig = S["ModelConfigOut"];
export type Conversation = S["ConversationOut"];
export type ConversationDetail = S["ConversationDetail"];
export type Message = S["MessageOut"];
export type Approval = S["ApprovalOut"];
export type Memory = S["MemoryOut"];
export type MemoryHit = S["MemorySearchHit"];
export type Integration = S["IntegrationOut"];
export type WorkspaceFile = S["WorkspaceFileOut"];
export type WorkspaceFileContent = S["WorkspaceFileContent"];
export type Preview = S["PreviewOut"];
export type Workflow = S["WorkflowOut"];
export type UsageSummary = S["UsageSummary"];
export type AuditEntry = S["AuditOut"];
export type JobCreated = S["JobCreated"];
export type RunCreated = S["RunCreated"];
export type TextToCad = S["TextToCadOut"];

export type JobState = Job["status"];
export const TERMINAL_STATES: JobState[] = ["completed", "failed", "cancelled", "partially_completed"];

/** Events streamed by POST /conversations/{id}/messages (text/event-stream). */
export type ChatEvent =
  | { type: "start"; user_message_id: string; assistant_message_id: string }
  | {
      type: "routing";
      model_config_id: string;
      model: string;
      display_name: string;
      provider: string;
      mode: "auto" | "manual";
      reason: string;
      fallback: boolean;
    }
  | { type: "delta"; text: string }
  | {
      type: "done";
      assistant_message_id: string;
      usage: { input_tokens: number; output_tokens: number; estimated: boolean };
      cost_usd: string | null;
      finish_reason: string | null;
    }
  | {
      type: "error";
      error: { kind: string; message: string; [k: string]: unknown };
      fallback_options?: { model_config_id: string; display_name: string; provider: string }[];
    };
