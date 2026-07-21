import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ArrowDown } from 'lucide-react';
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { EmptyMessage } from './Empty';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { TextInput } from '@/components/chat/TextInput.tsx';
import { Button } from '@/components/ui/button.tsx';
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
	const [showScrollToBottom, setShowScrollToBottom] = useState(false);

	const updateScrollState = useCallback(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const { scrollTop, scrollHeight, clientHeight } = scrollArea;
		const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

		wasNearBottomRef.current = distanceFromBottom <= 50;
		setShowScrollToBottom(distanceFromBottom > 100);
	}, []);

	const scrollToBottom = useCallback((behavior: ScrollBehavior) => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;
		scrollArea.scrollTo({
			top: scrollArea.scrollHeight,
			behavior,
		});
		setShowScrollToBottom(false);
	}, []);

	// Auto-scroll to bottom on session load, and after that only if the
	// user is already near the bottom.
	useLayoutEffect(() => {
		const currentCount = msgs.length;

		if (currentSessionIdRef.current !== sessionId) {
			currentSessionIdRef.current = sessionId;
			pendingSessionScrollRef.current = true;
			prevMsgCountRef.current = 0;
			wasNearBottomRef.current = true;
			setShowScrollToBottom(false);
		}

		const prevCount = prevMsgCountRef.current;
		const shouldScrollForSession = pendingSessionScrollRef.current && currentCount > 0;
		const isActive = phase !== 'idle';
		const hasRelevantUpdate =
			(currentCount > prevCount && prevCount > 0) || (isActive && prevCount > 0);
		const shouldScroll =
			shouldScrollForSession || (hasRelevantUpdate && wasNearBottomRef.current);

		if (shouldScroll) {
			scrollToBottom(shouldScrollForSession ? 'auto' : 'smooth');
			pendingSessionScrollRef.current = false;
			prevMsgCountRef.current = currentCount;
			return;
		}

		updateScrollState();
		prevMsgCountRef.current = currentCount;
	}, [msgs, phase, sessionId, scrollToBottom, updateScrollState]);

	// Track if user is near bottom whenever they scroll
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		scrollArea.addEventListener('scroll', updateScrollState);
		return () => scrollArea.removeEventListener('scroll', updateScrollState);
	}, [updateScrollState]);

	return (
		<div className={cn('flex flex-col h-full w-full items-center p-2 gap-4', className)}>
			<div className="relative flex-1 min-h-0 w-full max-w-full">
				<div
					ref={scrollAreaRef}
					className="size-full overflow-auto no-scrollbar overflow-x-hidden"
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
				<Button
					type="button"
					variant="outline"
					size="icon"
					aria-label="Scroll to bottom"
					aria-hidden={!showScrollToBottom}
					tabIndex={showScrollToBottom ? 0 : -1}
					className={cn(
						'absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full shadow-md transition-all duration-200',
						showScrollToBottom
							? 'translate-y-0 opacity-100'
							: 'pointer-events-none translate-y-2 opacity-0',
					)}
					onClick={() => scrollToBottom('smooth')}
				>
					<ArrowDown />
				</Button>
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
