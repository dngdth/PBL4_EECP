"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import {
  createPolicyProfileAction,
  updatePolicyProfileAction,
  type PolicyProfileActionState,
} from "@/features/policy-profiles/actions";
import type { PolicyProfile } from "@/features/policy-profiles/types";

type Props = { profile?: PolicyProfile };

const initialState: PolicyProfileActionState = { error: null, success: null };

function SaveButton({ editing }: { editing: boolean }) {
  const { pending } = useFormStatus();
  return (
    <button className="policy-form__submit" type="submit" disabled={pending}>
      {pending ? "Saving..." : editing ? "Save changes" : "Create policy"}
    </button>
  );
}

export function PolicyProfileForm({ profile }: Props) {
  const action = profile
    ? updatePolicyProfileAction.bind(null, profile.id)
    : createPolicyProfileAction;
  const [state, formAction] = useActionState(action, initialState);
  const rules = profile?.rules;

  return (
    <form action={formAction} className="policy-form">
      {!profile ? (
        <label>
          <span>Policy ID</span>
          <input name="id" placeholder="NO_AI_RESEARCH" minLength={2} maxLength={64} required />
        </label>
      ) : null}
      <label>
        <span>Display name</span>
        <input name="label" defaultValue={profile?.label} maxLength={100} required />
      </label>
      <label>
        <span>Description</span>
        <textarea name="description" defaultValue={profile?.description} maxLength={500} required />
      </label>
      <div className="policy-form__columns">
        <label>
          <span>Allowed applications</span>
          <textarea
            name="applications_allow"
            defaultValue={rules?.applications?.allow?.join("\n")}
            placeholder={"vscode.exe\ngcc.exe"}
          />
        </label>
        <label>
          <span>Denied applications</span>
          <textarea
            name="applications_deny"
            defaultValue={rules?.applications?.deny?.join("\n")}
            placeholder={"chatgpt.exe\nanydesk.exe"}
          />
        </label>
      </div>
      <label>
        <span>Blocked network categories</span>
        <textarea
          name="network_block"
          defaultValue={rules?.network?.block?.join("\n")}
          placeholder={"generative_ai\nsocial_network"}
        />
      </label>
      <label>
        <span>USB storage</span>
        <select name="usb" defaultValue={rules?.devices?.usb ?? "deny"}>
          <option value="deny">Deny</option>
          <option value="allow">Allow</option>
        </select>
      </label>
      <div className="policy-form__footer">
        <p className={state.error ? "policy-form__error" : "policy-form__success"} role="status">
          {state.error ?? state.success}
        </p>
        <SaveButton editing={Boolean(profile)} />
      </div>
    </form>
  );
}
