import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import React from 'react';
import { useRef, useEffect, useLayoutEffect } from 'react';

import { EmptyMessage } from './Empty';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { TextInput } from '@/components/chat/TextInput.tsx';
import type { ReplyPhase } from '@/hooks/useMessages';
import { cn } from '@/lib/utils';

interface ChatContentProps {
	sessionId: string | null;
	msgs: Msg[];
	/**
	 * Reply lifecycle phase from ``useMessages`` — forwarded to
	 * ``TextInput`` so the single send / stop button can pick its
	 * icon, tooltip, disabled state and click handler from one source.
	 */
	phase: ReplyPhase;
	disabled: boolean;
	onSend: (content: ContentBlock[]) => void;
	onUserConfirm: (
		toolCall: ToolCallBlock,
		confirm: boolean,
		replyId: string,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
	onOpenDiagnostics?: (replyId: string) => void;
	autoComplete?: (input: string) => string | null;
	className?: string;
	/** Called when the user clicks the stop button. */
	onInterrupt?: () => void;
	/**
	 * Optional content pinned at the bottom of the chat — between the
	 * message scroll area and the text input (e.g. pending subagent HITL
	 * cards on a team leader's view). Rendered below the conversation so
	 * a pending confirmation sits next to the input, where the user is
	 * looking, rather than scrolled off the top.
	 */
	footerSlot?: React.ReactNode;
	/** @see TextInputProps.allowedInputTypes */
	allowedInputTypes: string[];
	/** @see TextInputProps.fileProcessor */
	fileProcessor: (file: File) => Promise<ContentBlock | null>;
}

const ChatContentComponent: React.FC<ChatContentProps> = ({
	msgs,
	phase,
	disabled,
	onSend,
	onUserConfirm,
	onOpenDiagnostics,
	autoComplete,
	className,
	onInterrupt,
	footerSlot,
	allowedInputTypes,
	fileProcessor,
	sessionId,
}) => {
	const scrollAreaRef = useRef<HTMLDivElement>(null);
	const currentSessionIdRef = useRef<string | null>(null);
	const prevMsgCountRef = useRef<number>(0);
	const wasNearBottomRef = useRef<boolean>(true);
	const pendingSessionScrollRef = useRef<boolean>(true);
	const waitingForSessionMessagesRef = useRef<boolean>(false);

	// Auto-scroll to bottom on session load, and after that only if the
	// user is already near the bottom.
	useLayoutEffect(() => {
		const currentCount = msgs.length;
		if (currentSessionIdRef.current !== sessionId) {
			currentSessionIdRef.current = sessionId;
			pendingSessionScrollRef.current = true;
			waitingForSessionMessagesRef.current = currentCount > 0;
			prevMsgCountRef.current = 0;
			wasNearBottomRef.current = true;
		}

		const prevCount = prevMsgCountRef.current;
		if (pendingSessionScrollRef.current && waitingForSessionMessagesRef.current) {
			if (currentCount === 0) {
				waitingForSessionMessagesRef.current = false;
				prevMsgCountRef.current = 0;
			}
			return;
		}

		const shouldScrollForSession = pendingSessionScrollRef.current && currentCount > 0;

		const isActive = phase !== 'idle';
		const shouldCheck =
			shouldScrollForSession ||
			(currentCount > prevCount && prevCount > 0) ||
			(isActive && prevCount > 0);

		if (shouldCheck && scrollAreaRef.current) {
			const { scrollHeight } = scrollAreaRef.current;

			// Check if user was near bottom before content changed
			const isNearBottom = wasNearBottomRef.current;

			if (shouldScrollForSession || isNearBottom) {
				scrollAreaRef.current.scrollTo({
					top: scrollHeight,
					behavior: shouldScrollForSession ? 'auto' : 'smooth',
				});
				pendingSessionScrollRef.current = false;
			}
		}

		prevMsgCountRef.current = currentCount;
	}, [msgs, phase, sessionId]);

	// Track if user is near bottom whenever they scroll
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const handleScroll = () => {
			const { scrollTop, scrollHeight, clientHeight } = scrollArea;
			wasNearBottomRef.current = scrollTop + clientHeight >= scrollHeight - 50;
		};

		scrollArea.addEventListener('scroll', handleScroll);
		return () => scrollArea.removeEventListener('scroll', handleScroll);
	}, []);

	return (
		<div className={cn('flex flex-col h-full w-full items-center p-2 gap-4', className)}>
			<div
				ref={scrollAreaRef}
				className="flex-1 w-full max-w-full overflow-auto no-scrollbar overflow-x-hidden"
			>
				<div className="flex flex-col gap-4 size-full max-w-full">
					{msgs.length > 0 ? (
						msgs.map((message) => (
							<MessageBubble
								key={message.id}
								message={message}
								onUserConfirm={onUserConfirm}
								onOpenDiagnostics={
									message.role === 'assistant' ? onOpenDiagnostics : undefined
								}
							/>
						))
					) : (
						<EmptyMessage />
					)}
				</div>
			</div>
			{footerSlot ? <div className="w-full max-w-full shrink-0">{footerSlot}</div> : null}
			<TextInput
				className="min-w-full max-w-full w-full"
				onSend={onSend}
				disabled={disabled}
				autoComplete={autoComplete}
				allowedInputTypes={allowedInputTypes}
				fileProcessor={fileProcessor}
				phase={phase}
				onInterrupt={onInterrupt}
			/>
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
