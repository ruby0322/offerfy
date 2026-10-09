"use client";

import { Check, ChevronDown, Plus } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { FamilyMember, ResumeFamily } from "@/lib/api";
import { cn } from "@/lib/utils";

type Labels = {
  menu: string;
  master: string;
  tailor: string;
  newTailor: string;
  blocked: string;
};

type Props = {
  family: ResumeFamily | null;
  currentId: string;
  title: string;
  disabled: boolean;
  onSelect: (id: string) => void;
  onNewTailor: () => void;
  labels: Labels;
};

function KindTag({ kind, labels }: { kind: FamilyMember["kind"]; labels: Labels }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-sm border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide",
        kind === "master"
          ? "border-foreground/20 text-foreground/70"
          : "border-primary/40 text-primary",
      )}
    >
      {kind === "master" ? labels.master : labels.tailor}
    </span>
  );
}

function MemberRow({
  member,
  current,
  labels,
  onSelect,
}: {
  member: FamilyMember;
  current: boolean;
  labels: Labels;
  onSelect: (id: string) => void;
}) {
  return (
    <DropdownMenuItem
      data-testid={`family-item-${member.id}`}
      onSelect={() => {
        if (!current) onSelect(member.id);
      }}
      className="items-start"
    >
      <Check aria-hidden="true" className={cn("mt-0.5", current ? "opacity-100" : "opacity-0")} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate">{member.title}</span>
          <KindTag kind={member.kind} labels={labels} />
        </span>
        {member.target_label ? (
          <span className="truncate text-xs text-muted-foreground">{member.target_label}</span>
        ) : null}
      </span>
    </DropdownMenuItem>
  );
}

export default function ResumeFamilyMenu({
  family,
  currentId,
  title,
  disabled,
  onSelect,
  onNewTailor,
  labels,
}: Props) {
  const currentKind =
    family && family.master.id !== currentId ? "tailor" : ("master" as const);

  const trigger = (
    <button
      type="button"
      aria-label={labels.menu}
      data-testid="family-menu-trigger"
      disabled={disabled}
      className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
    >
      <span className="truncate">{title}</span>
      {family ? <KindTag kind={currentKind} labels={labels} /> : null}
      <ChevronDown aria-hidden="true" className="size-3.5 shrink-0" />
    </button>
  );

  if (disabled) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex min-w-0" tabIndex={0}>
            {trigger}
          </span>
        </TooltipTrigger>
        <TooltipContent>{labels.blocked}</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        {family ? (
          <>
            <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
              {labels.master}
            </DropdownMenuLabel>
            <MemberRow
              member={family.master}
              current={family.master.id === currentId}
              labels={labels}
              onSelect={onSelect}
            />
            {family.tailors.length > 0 ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
                  {labels.tailor}
                </DropdownMenuLabel>
                {family.tailors.map((member) => (
                  <MemberRow
                    key={member.id}
                    member={member}
                    current={member.id === currentId}
                    labels={labels}
                    onSelect={onSelect}
                  />
                ))}
              </>
            ) : null}
            <DropdownMenuSeparator />
          </>
        ) : null}
        <DropdownMenuItem data-testid="family-new-tailor" onSelect={onNewTailor}>
          <Plus aria-hidden="true" />
          {labels.newTailor}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
