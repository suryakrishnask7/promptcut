"""
OperationLog – the append-only, undo/redo-aware history of edits.

Immutability strategy
---------------------
Every mutating method (push, undo, redo, restore_op) returns a *new*
OperationLog rather than mutating self.  This makes the engine a pure function
(same log → same timeline) and lets tests snapshot state cheaply.

Undo / redo vs restore_op
--------------------------
- ``pointer`` is the index up to which ops are "in scope".
  Engine applies ops at indices 0 … pointer-1 where active == True.
- undo()  → pointer -= 1  (op is still in the list, just out of scope)
- redo()  → pointer += 1
- push()  → truncates ops to [:pointer], appends new op, pointer = len(ops)
            (this clears redo history – spec §4)
- restore_op(op_id) → toggles op.active for ONE op within scope WITHOUT
            touching pointer or any other op  (spec §4 "restore one specific
            earlier edit")

op_id assignment
----------------
op_ids are monotonically increasing integers, never reused, assigned by
push().  The counter is derived from max(op.op_id) + 1 so it survives
round-trips through JSON without a separate stored counter field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from promptcut.models.operations import Operation


class OperationLog(BaseModel):
    """
    Append-only operation history with undo/redo pointer.

    ops     – all operations ever pushed (including ones beyond ``pointer``
              that could be redo'd, until a new push clears them).
    pointer – how many ops are currently "in scope" (0 = fully undone).
    """

    ops: list[Operation] = Field(default_factory=list)
    pointer: int = Field(default=0, ge=0)
    action_history: list[tuple[str, int]] = Field(default_factory=list)
    action_redo: list[tuple[str, int]] = Field(default_factory=list)

    # Private counter; not serialised – reconstructed from ops on load.
    _next_op_id: int = PrivateAttr(default=0)

    @model_validator(mode="after")
    def _init_counter(self) -> "OperationLog":
        # Derive next id from the highest id seen, so deserialized logs keep
        # the monotone guarantee even after round-tripping through JSON.
        if self.ops:
            self._next_op_id = max(op.op_id for op in self.ops) + 1
        else:
            self._next_op_id = 0
        if self.pointer > len(self.ops):
            raise ValueError(
                f"pointer ({self.pointer}) > len(ops) ({len(self.ops)})"
            )
        # If action_history was not explicitly provided on load, backfill from ops
        if not self.action_history and self.pointer > 0:
            object.__setattr__(
                self,
                "action_history",
                [("push", op.op_id) for op in self.ops[: self.pointer]],
            )
        return self

    # ------------------------------------------------------------------
    # Read-only helpers
    # ------------------------------------------------------------------

    @property
    def active_ops(self) -> list[Operation]:
        """Ops that the engine will apply: in-scope AND active."""
        return [op for i, op in enumerate(self.ops) if i < self.pointer and op.active]

    @property
    def can_undo(self) -> bool:
        return len(self.action_history) > 0 or self.pointer > 0

    @property
    def can_redo(self) -> bool:
        return len(self.action_redo) > 0 or self.pointer < len(self.ops)

    # ------------------------------------------------------------------
    # Mutating operations (each returns a new OperationLog)
    # ------------------------------------------------------------------

    def push(self, op: Operation, interpreted_by: str | None = None) -> "OperationLog":
        """
        Append a new operation, clearing any redo history.

        The caller should leave op.op_id == -1; push() assigns the real id.
        Optionally sets interpreted_by ("rule", "llm", "cache").
        """
        # Assign deterministic id and optional interpreted_by stamp
        assigned_id = self._next_op_id
        update_fields: dict[str, Any] = {"op_id": assigned_id}
        if interpreted_by is not None:
            update_fields["interpreted_by"] = interpreted_by
        elif getattr(op, "interpreted_by", None) is not None:
            update_fields["interpreted_by"] = op.interpreted_by
        op_with_id: Operation = op.model_copy(update=update_fields)
        # Truncate to pointer (drops redo history) then append
        new_ops = list(self.ops[: self.pointer]) + [op_with_id]
        new_history = [
            action for action in self.action_history
            if action[0] != "push" or any(o.op_id == action[1] for o in self.ops[: self.pointer])
        ] + [("push", assigned_id)]
        return OperationLog(
            ops=new_ops,
            pointer=len(new_ops),
            action_history=new_history,
            action_redo=[],
        )

    def undo(self) -> "OperationLog":
        """Reverses the last applied instruction (push or restore_op)."""
        if self.action_history:
            action_type, target_id = self.action_history[-1]
            new_history = self.action_history[:-1]
            new_redo = list(self.action_redo) + [(action_type, target_id)]

            if action_type == "restore":
                # Invert active flag back on target_id
                new_ops = [
                    op.model_copy(update={"active": not op.active})
                    if op.op_id == target_id
                    else op
                    for op in self.ops
                ]
                return OperationLog(
                    ops=new_ops,
                    pointer=self.pointer,
                    action_history=new_history,
                    action_redo=new_redo,
                )
            else:
                # Push action undone: decrement pointer
                new_ptr = max(0, self.pointer - 1)
                return OperationLog(
                    ops=list(self.ops),
                    pointer=new_ptr,
                    action_history=new_history,
                    action_redo=new_redo,
                )

        if self.pointer <= 0:
            return self
        return OperationLog(ops=list(self.ops), pointer=self.pointer - 1)

    def redo(self) -> "OperationLog":
        """Re-applies the last undone instruction (push or restore_op)."""
        if self.action_redo:
            action_type, target_id = self.action_redo[-1]
            new_redo = self.action_redo[:-1]
            new_history = list(self.action_history) + [(action_type, target_id)]

            if action_type == "restore":
                # Invert active flag on target_id
                new_ops = [
                    op.model_copy(update={"active": not op.active})
                    if op.op_id == target_id
                    else op
                    for op in self.ops
                ]
                return OperationLog(
                    ops=new_ops,
                    pointer=self.pointer,
                    action_history=new_history,
                    action_redo=new_redo,
                )
            else:
                new_ptr = min(len(self.ops), self.pointer + 1)
                return OperationLog(
                    ops=list(self.ops),
                    pointer=new_ptr,
                    action_history=new_history,
                    action_redo=new_redo,
                )

        if self.pointer >= len(self.ops):
            return self
        return OperationLog(ops=list(self.ops), pointer=self.pointer + 1)

    def restore_op(self, op_id: int) -> "OperationLog":
        """
        Toggle the ``active`` flag of the op with the given id.

        Only ops within the current scope (index < pointer) may be toggled.
        Raises ValueError if op_id is not found within scope.
        """
        found = False
        new_ops: list[Operation] = []
        for i, op in enumerate(self.ops):
            if op.op_id == op_id and i < self.pointer:
                # Toggle: active → inactive, or inactive → active
                new_ops.append(op.model_copy(update={"active": not op.active}))
                found = True
            else:
                new_ops.append(op)
        if not found:
            raise ValueError(
                f"op_id {op_id} not found within current scope (pointer={self.pointer})"
            )
        new_history = list(self.action_history) + [("restore", op_id)]
        return OperationLog(
            ops=new_ops,
            pointer=self.pointer,
            action_history=new_history,
            action_redo=[],
        )

    # ------------------------------------------------------------------
    # Duplicate detection helper
    # ------------------------------------------------------------------

    def has_equivalent(self, op: Operation) -> bool:
        """
        Return True if an equivalent op already exists in the active log.

        "Equivalent" = same op_type and same payload fields (excluding op_id
        and active).  Used by the response layer to silently skip duplicates.
        """
        candidate = op.model_dump(exclude={"op_id", "active", "interpreted_by"})
        for active_op in self.active_ops:
            if active_op.model_dump(exclude={"op_id", "active", "interpreted_by"}) == candidate:
                return True
        return False
