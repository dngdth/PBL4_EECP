"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import {
  deletePolicyProfileAction,
  type PolicyProfileActionState,
} from "@/features/policy-profiles/actions";

const initialState: PolicyProfileActionState = { error: null, success: null };

function DeleteButton() {
  const { pending } = useFormStatus();
  return (
    <button className="policy-delete__button" type="submit" disabled={pending}>
      {pending ? "Deleting..." : "Delete policy"}
    </button>
  );
}

export function DeletePolicyForm({ profileId }: { profileId: string }) {
  const [state, action] = useActionState(
    deletePolicyProfileAction.bind(null, profileId),
    initialState,
  );
  return (
    <form
      action={action}
      className="policy-delete"
      onSubmit={(event) => {
        if (!window.confirm(`Delete policy ${profileId}?`)) event.preventDefault();
      }}
    >
      <p role="status">{state.error ?? state.success}</p>
      <DeleteButton />
    </form>
  );
}
