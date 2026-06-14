import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';

import { track } from '../../lib/analytics';
import { registerForPushNotifications } from '../../lib/notifications';
import { flattenAnswers, saveAnswers, type OnboardingAnswers } from '../../lib/onboardingProfile';
import { fonts } from '../../lib/theme';
import { ChoiceGroup } from './ChoiceGroup';
import { HoldToCommit } from './HoldToCommit';
import { OnboardingScaffold } from './OnboardingScaffold';

const AGE = ['Under 18', '18–24', '25–34', '35–44', '45–54', '55+'];
const GENDER = ['Female', 'Male', 'Non-binary', 'Prefer not to say'];
const OCCUPATION = ['Student', 'Working professional', 'Self-employed', 'Parent / caregiver', 'Between things', 'Other'];
const FAMILIARITY = [
  'I journal regularly',
  "I've tried, but it never stuck",
  'I want to, but lack the motivation',
  "I'm completely new to it",
];
const ISSUES = ['Work', 'School', 'Relationships', 'Family', 'Finances', 'General stress & anxiety'];
const TRIED = ['Yes — many times', 'Once or twice', 'Never tried'];

const TAILORED: Record<string, { title: string; body: string }> = {
  Work: { title: 'Work weighs heavy.', body: 'Regular reflection is shown to lower burnout and help you actually switch off at night.' },
  School: { title: 'School is a lot.', body: 'Writing it down clears the mental clutter, so you can focus — and remember more.' },
  Relationships: { title: 'Relationships are tender.', body: 'Naming what you feel helps you respond with intention, instead of reacting in the moment.' },
  Family: { title: 'Family stays with you.', body: 'Reflection creates a little space between you and the noise — and a lot more patience.' },
  Finances: { title: 'Money is stressful.', body: 'Getting the worry out of your head and onto the page quiets the late-night spiral.' },
  'General stress & anxiety': { title: "You're carrying a lot.", body: 'Journaling is one of the most studied ways to calm an anxious, racing mind.' },
};

const STEPS = [
  'name', 'age', 'gender', 'occupation',
  'familiarity', 'issues',
  'benefit', 'tailored', 'tried', 'reread', 'reveal1', 'reveal2',
  'commit', 'notifications',
] as const;

function Heading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <View style={{ marginBottom: 22 }}>
      <Text style={{ fontFamily: fonts.serifMedium, fontSize: 26, lineHeight: 33, color: '#2A2825' }}>{title}</Text>
      {subtitle ? (
        <Text style={{ fontFamily: fonts.sans, fontSize: 15, lineHeight: 22, color: '#8E8B84', marginTop: 8 }}>{subtitle}</Text>
      ) : null}
    </View>
  );
}

function StoryView({ title, body }: { title: string; body: string }) {
  return (
    <View style={{ flex: 1, justifyContent: 'center' }}>
      <Text style={{ fontFamily: fonts.serifMedium, fontSize: 30, lineHeight: 39, color: '#2A2825' }}>{title}</Text>
      <Text style={{ fontFamily: fonts.serif, fontSize: 19, lineHeight: 30, color: '#6E6B64', marginTop: 16 }}>{body}</Text>
    </View>
  );
}

function PrimaryButton({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={{
        height: 54,
        borderRadius: 18,
        backgroundColor: '#2A2825',
        justifyContent: 'center',
        alignItems: 'center',
        opacity: disabled ? 0.35 : 1,
      }}
    >
      <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#fff' }}>{label}</Text>
    </Pressable>
  );
}

export function OnboardingFlow({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<OnboardingAnswers>({ familiarity: [], issues: [] });
  const startedRef = useRef(false);

  useEffect(() => {
    if (!startedRef.current) {
      startedRef.current = true;
      track('onboarding_started');
    }
  }, []);

  const key = STEPS[step];

  useEffect(() => {
    track('onboarding_step_viewed', { step, key });
  }, [step, key]);

  function set<K extends keyof OnboardingAnswers>(k: K, v: OnboardingAnswers[K]) {
    setAnswers((a) => ({ ...a, [k]: v }));
    track('onboarding_answer', { key: k, value: Array.isArray(v) ? v.join(',') : String(v ?? '') });
  }

  const back = step > 0 ? () => setStep((s) => Math.max(0, s - 1)) : undefined;
  const next = () => setStep((s) => Math.min(STEPS.length - 1, s + 1));

  const finish = async () => {
    await saveAnswers(answers);
    const flat = flattenAnswers(answers);
    track('onboarding_completed', { ...flat, $set: flat });
    onDone();
  };

  const enableNotifications = async () => {
    await registerForPushNotifications();
    finish();
  };

  const progress = (step + 1) / STEPS.length;
  const topIssue = answers.issues?.[0] ?? 'General stress & anxiety';
  const tailored = TAILORED[topIssue] ?? TAILORED['General stress & anxiety'];

  let content: ReactNode = null;
  let footer: ReactNode = null;

  switch (key) {
    case 'name':
      content = (
        <>
          <Heading title="First — what should we call you?" />
          <TextInput
            value={answers.name ?? ''}
            onChangeText={(t) => setAnswers((a) => ({ ...a, name: t }))}
            placeholder="Your first name"
            placeholderTextColor="#B4B1A9"
            autoFocus
            returnKeyType="done"
            onSubmitEditing={() => answers.name?.trim() && next()}
            style={{
              fontFamily: fonts.serif,
              fontSize: 24,
              color: '#2A2825',
              borderBottomWidth: 1,
              borderBottomColor: '#DDD8D0',
              paddingVertical: 10,
            }}
          />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.name?.trim()} />;
      break;

    case 'age':
      content = (
        <>
          <Heading
            title={`How old are you${answers.name?.trim() ? `, ${answers.name.trim()}` : ''}?`}
            subtitle="This helps us tune your reflections."
          />
          <ChoiceGroup options={AGE} value={answers.age} onChange={(v) => set('age', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.age} />;
      break;

    case 'gender':
      content = (
        <>
          <Heading title="Which best describes you?" />
          <ChoiceGroup options={GENDER} value={answers.gender} onChange={(v) => set('gender', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.gender} />;
      break;

    case 'occupation':
      content = (
        <>
          <Heading title="What fills most of your days?" />
          <ChoiceGroup options={OCCUPATION} value={answers.occupation} onChange={(v) => set('occupation', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.occupation} />;
      break;

    case 'familiarity':
      content = (
        <>
          <Heading title="How do you relate to journaling?" subtitle="Pick all that feel true." />
          <ChoiceGroup multi options={FAMILIARITY} value={answers.familiarity ?? []} onChange={(v) => set('familiarity', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.familiarity?.length} />;
      break;

    case 'issues':
      content = (
        <>
          <Heading title="What's weighing on you right now?" subtitle="Pick all that apply." />
          <ChoiceGroup multi options={ISSUES} value={answers.issues ?? []} onChange={(v) => set('issues', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.issues?.length} />;
      break;

    case 'benefit':
      content = (
        <StoryView
          title="Journaling works."
          body="It's scientifically shown to ease anxiety, bring emotional clarity, and even improve your sleep and focus."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'tailored':
      content = <StoryView title={tailored.title} body={tailored.body} />;
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'tried':
      content = (
        <>
          <Heading title="Be honest — have you tried journaling before, and stopped?" />
          <ChoiceGroup options={TRIED} value={answers.tried_before} onChange={(v) => set('tried_before', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.tried_before} />;
      break;

    case 'reread':
      content = (
        <StoryView
          title="You're not alone."
          body="Most of journaling's power comes from rereading your past entries — and almost nobody ever does. It's the part that quietly gets skipped."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'reveal1':
      content = (
        <StoryView
          title="So we made it effortless."
          body="JAI turns journaling into a warm conversation. You just talk — it listens, remembers, and gently asks the right questions."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'reveal2':
      content = (
        <StoryView
          title="And it rereads for you."
          body="JAI quietly connects the dots across your days, tracks how you're really doing, and is ready with grounded advice whenever you ask."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'commit':
      content = (
        <View style={{ flex: 1, justifyContent: 'center' }}>
          <Text style={{ fontFamily: fonts.serifMedium, fontSize: 30, lineHeight: 39, color: '#2A2825' }}>
            Do you want to transform your life?
          </Text>
          <Text style={{ fontFamily: fonts.serif, fontSize: 18, lineHeight: 28, color: '#6E6B64', marginTop: 16 }}>
            It starts with a small, daily promise to yourself. Hold the button to commit.
          </Text>
        </View>
      );
      footer = <HoldToCommit label="Hold to commit" onComplete={next} />;
      break;

    case 'notifications':
      content = (
        <View style={{ flex: 1, justifyContent: 'center' }}>
          <Text style={{ fontFamily: fonts.serifMedium, fontSize: 30, lineHeight: 39, color: '#2A2825' }}>
            One last thing.
          </Text>
          <Text style={{ fontFamily: fonts.serif, fontSize: 18, lineHeight: 28, color: '#6E6B64', marginTop: 16 }}>
            Each morning, JAI sends a gentle reflection on your yesterday — the nudge that keeps the habit alive.
          </Text>
        </View>
      );
      footer = (
        <View style={{ gap: 12 }}>
          <PrimaryButton label="Enable notifications" onPress={enableNotifications} />
          <Pressable onPress={finish} hitSlop={8} style={{ alignItems: 'center', paddingVertical: 4 }}>
            <Text style={{ fontFamily: fonts.sans, fontSize: 13, color: '#9A9790' }}>Maybe later</Text>
          </Pressable>
        </View>
      );
      break;
  }

  return (
    <OnboardingScaffold progress={progress} onBack={back} footer={footer}>
      {content}
    </OnboardingScaffold>
  );
}
