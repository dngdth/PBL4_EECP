"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import {
  transitionSessionAction,
  type TransitionSessionActionState,
} from "@/features/exam-sessions/actions";

type TransitionFormProps = {
  sessionId: string;
  target: "READY" | "RUNNING" | "FINISHED";
  label: string;
};

const initialState: TransitionSessionActionState = { error: null };

function TransitionButton({ label }: Pick<TransitionFormProps, "label">) {
  const { pending } = useFormStatus();

  return (
    <button type="submit" disabled={pending}>
      {pending ? "Updating session..." : label}
    </button>
  );
}

export function TransitionForm({ sessionId, target, label }: TransitionFormProps) {
  const [state, formAction] = useActionState(
    transitionSessionAction.bind(null, sessionId, target),
    initialState,
  );

  return (
    <form action={formAction} className="session-card__action">
      <p
        className="session-card__action-error"
        role="status"
        aria-live="polite"
      >
        {state.error}
      </p>
      <TransitionButton label={label} />
    </form>
  );
}
