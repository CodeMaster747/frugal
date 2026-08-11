"use client";

import { use } from "react";

import { ReceiptReview } from "@/features/receipts/components/review";

export default function ReceiptReviewPage({ params }: PageProps<"/receipts/[id]">) {
  // Route params are Promises in Next 16; `use` unwraps them in a client
  // component.
  const { id } = use(params);
  return <ReceiptReview receiptId={id} />;
}
