"use client";

/**
 * The category control for a transaction row.
 *
 * The whole of M5 reaches the user through this one component, so it carries
 * the thing that distinguishes a suggestion from a fact: a machine guess is
 * visibly provisional until someone confirms it, and the reason it was made is
 * one hover away.
 *
 * Confidence is deliberately *not* a number in the UI. "0.85" invites the user
 * to calibrate against a scale nobody explained; a dot plus a plain-language
 * title says the same thing without pretending to a precision the model does
 * not have.
 */

import { Check } from "lucide-react";
import { useState } from "react";

import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";

import type { Transaction } from "../api";

interface Props {
  txn: Transaction;
  categories: { id: string; name: string; kind: string }[];
  onRecategorize: (categoryId: string) => void;
  onConfirm: () => void;
  busy?: boolean;
}

/** Plain language beats a decimal the user has no scale for. */
function describeConfidence(txn: Transaction): string {
  if (txn.is_reviewed) return "Confirmed by you";
  if (!txn.category_confidence) return "Category set manually";

  const confidence = Number(txn.category_confidence);
  const basis = txn.categorizer_version?.startsWith("rules")
    ? "matched a known merchant"
    : "predicted from the merchant name";

  if (confidence >= 0.9) return `Very likely — ${basis}`;
  if (confidence >= 0.6) return `Likely — ${basis}`;
  return `Uncertain — ${basis}. Worth a look.`;
}

export function CategoryCell({ txn, categories, onRecategorize, onConfirm, busy }: Props) {
  const [open, setOpen] = useState(false);
  const suggested = txn.category !== null && !txn.is_reviewed;
  const confidence = txn.category_confidence ? Number(txn.category_confidence) : null;

  return (
    <span className="flex items-center gap-1.5">
      {suggested && (
        <span
          // Identity is never colour-alone: the dot is decorative and the real
          // signal is the title text, which screen readers and hovers both get.
          className={`size-1.5 shrink-0 rounded-full ${
            confidence !== null && confidence < 0.6 ? "bg-warning" : "bg-series-1"
          }`}
          title={describeConfidence(txn)}
          aria-hidden
        />
      )}

      <Select
        id={`cat-${txn.id}`}
        size="sm"
        hideLabel
        label={`Category for ${txn.merchant_raw ?? "transaction"}${
          suggested ? ` — suggested, ${describeConfidence(txn)}` : ""
        }`}
        // A dashed border marks a machine suggestion the same way `Empty` marks
        // absent content: the value is provisional until confirmed.
        className={cn("w-auto", suggested && "border-dashed border-series-1/60")}
        value={txn.category?.id ?? ""}
        disabled={busy}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(e) => onRecategorize(e.target.value)}
      >
        <option value="">Uncategorised</option>
        {categories
          .filter((c) => c.kind === txn.kind || txn.kind === "transfer")
          .map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
      </Select>

      {suggested && (
        // Confirming is one click and it is the common case: most suggestions
        // are right, and making "yes" as cheap as "no" is what keeps the queue
        // from feeling like a chore.
        <button
          type="button"
          className="grid size-7 shrink-0 place-items-center rounded-control text-ink-muted transition-colors hover:bg-surface-raised hover:text-good disabled:opacity-50"
          title={`Confirm ${txn.category?.name ?? "category"}`}
          aria-label={`Confirm ${txn.category?.name ?? "category"} for ${
            txn.merchant_raw ?? "transaction"
          }`}
          disabled={busy}
          onClick={onConfirm}
        >
          <Check className="size-3.5" aria-hidden />
        </button>
      )}
      {open && suggested && (
        <span className="sr-only" role="status">
          {describeConfidence(txn)}
        </span>
      )}
    </span>
  );
}
