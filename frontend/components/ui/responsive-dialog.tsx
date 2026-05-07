'use client';

import * as React from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from '@/components/ui/drawer';

type AnyProps = Record<string, unknown> & { children?: React.ReactNode };

export function ResponsiveDialog({
  open,
  onOpenChange,
  children,
}: {
  open?: boolean;
  onOpenChange?: (o: boolean) => void;
  children: React.ReactNode;
}) {
  const isMobile = useIsMobile();
  if (isMobile) {
    return (
      <Drawer open={open} onOpenChange={onOpenChange}>
        {children}
      </Drawer>
    );
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {children}
    </Dialog>
  );
}

export function ResponsiveDialogTrigger(props: AnyProps) {
  const isMobile = useIsMobile();
  return isMobile ? <DrawerTrigger {...props} /> : <DialogTrigger {...props} />;
}

export function ResponsiveDialogClose(props: AnyProps) {
  const isMobile = useIsMobile();
  return isMobile ? <DrawerClose {...props} /> : <DialogClose {...props} />;
}

export function ResponsiveDialogContent(props: AnyProps) {
  const isMobile = useIsMobile();
  if (isMobile) {
    // vaul's DrawerContent doesn't accept showCloseButton - strip it so React
    // doesn't warn about an unknown DOM attribute.
    const { showCloseButton: _showCloseButton, ...rest } = props as AnyProps & {
      showCloseButton?: boolean;
    };
    return <DrawerContent {...rest} />;
  }
  return <DialogContent {...props} />;
}

export function ResponsiveDialogHeader(props: AnyProps) {
  const isMobile = useIsMobile();
  return isMobile ? <DrawerHeader {...props} /> : <DialogHeader {...props} />;
}

export function ResponsiveDialogFooter(props: AnyProps) {
  const isMobile = useIsMobile();
  return isMobile ? <DrawerFooter {...props} /> : <DialogFooter {...props} />;
}

export function ResponsiveDialogTitle(props: AnyProps) {
  const isMobile = useIsMobile();
  return isMobile ? <DrawerTitle {...props} /> : <DialogTitle {...props} />;
}

export function ResponsiveDialogDescription(props: AnyProps) {
  const isMobile = useIsMobile();
  return isMobile ? <DrawerDescription {...props} /> : <DialogDescription {...props} />;
}
