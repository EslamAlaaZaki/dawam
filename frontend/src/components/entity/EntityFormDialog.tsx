import type { FormEvent, ReactNode } from "react";

import { ErrorText } from "../../admin/parts";
import { Button } from "../ui/button";
import { useReturnFocus } from "./useReturnFocus";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";

/**
 * The popup form for creating or editing an entity: the fields are the children, the
 * values arrive as FormData. It unmounts on close, so the next open starts fresh, and
 * Radix returns focus to the control that opened it.
 */
export function EntityFormDialog({
  open,
  onOpenChange,
  title,
  description,
  submitLabel,
  onSubmit,
  pending,
  error,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  submitLabel: string;
  onSubmit: (form: FormData) => void;
  pending?: boolean;
  error?: Error | null;
  children: ReactNode;
}) {
  const returnFocus = useReturnFocus(open);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(new FormData(event.currentTarget));
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onCloseAutoFocus={returnFocus}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form className="flex flex-col gap-3.5" onSubmit={submit}>
          {children}
          {error && <ErrorText>{error.message}</ErrorText>}
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="ghost">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" variant="primary" disabled={pending}>
              {submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
