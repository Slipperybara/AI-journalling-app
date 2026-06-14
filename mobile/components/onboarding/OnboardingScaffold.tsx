import type { ReactNode } from 'react';
import { Platform, Pressable, Text, View } from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ProgressBar } from './ProgressBar';

// Shared onboarding layout: progress bar + back chevron on top, scrollable
// content, and a pinned footer (CTA). Keyboard-avoiding for the text steps.
export function OnboardingScaffold({
  progress,
  onBack,
  children,
  footer,
}: {
  progress: number;
  onBack?: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <SafeAreaView className="flex-1 bg-paper">
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: 8 }}>
          <ProgressBar progress={progress} />
          <View style={{ height: 30, justifyContent: 'center' }}>
            {onBack ? (
              <Pressable onPress={onBack} hitSlop={12} style={{ alignSelf: 'flex-start' }}>
                <Text style={{ fontSize: 26, color: '#B4B1A9', marginTop: -2 }}>‹</Text>
              </Pressable>
            ) : null}
          </View>
        </View>
        <View style={{ flex: 1, paddingHorizontal: 28, paddingTop: 6 }}>{children}</View>
        <View style={{ paddingHorizontal: 28, paddingBottom: 20 }}>{footer}</View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
