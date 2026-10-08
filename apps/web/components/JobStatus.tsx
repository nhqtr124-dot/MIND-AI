"use client";

import type { Job } from "@mind/shared-types";
import { api } from "@/lib/api";
import { Alert, Button, Progress, StatusBadge } from "./ui";

/** Live job progress with honest terminal states and cancellation. */
export function JobStatus({ job, onCancelled }: { job: Job | null; onCancelled?: () => void }) {
  if (!job) return null;
  const active = job.status === "planned" || job.status === "running";
  const err = job.error as { message?: string } | null;
  return (
    <div className="space-y-2 rounded-[12px] border border-border bg-surface-2/40 p-3">
      <div className="flex items-center gap-2 text-sm">
        <StatusBadge status={job.status} />
        <span className="min-w-0 flex-1 truncate text-muted">{job.message}</span>
        {active && (
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: job.id } } });
              onCancelled?.();
            }}
          >
            Cancel
          </Button>
        )}
      </div>
      {active && <Progress value={job.progress} />}
      {job.status === "failed" && err?.message && <Alert>{err.message}</Alert>}
    </div>
  );
}
