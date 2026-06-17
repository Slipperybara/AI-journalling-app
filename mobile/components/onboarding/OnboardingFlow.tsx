import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';

import { track } from '../../lib/analytics';
import { registerForPushNotifications } from '../../lib/notifications';
import { flattenAnswers, saveAnswers, type OnboardingAnswers } from '../../lib/onboardingProfile';
import { fonts } from '../../lib/theme';
import { Mascot, type MascotMood } from '../Mascot';
import { ChoiceGroup } from './ChoiceGroup';
import { HoldToCommit } from './HoldToCommit';
import { OnboardingScaffold } from './OnboardingScaffold';

const AGE = ['Under 18', '18–24', '25–34', '35–44', '45–54', '55+'];
const GENDER = ['Female', 'Male', 'Non-binary', 'Prefer not to say'];
const OCCUPATION = ['Student', 'Working professional', 'Self-employed', 'Parent / caregiver', 'Between things', 'Other'];
// First option is the "super positive" state — it reframes the later issues page
// ("what's weighing on you" → "what could be even better").
const EMOTIONAL = ['I feel great', 'Pretty good', 'Up and down', 'Running low', 'Really struggling'];
const SUPER_POSITIVE = EMOTIONAL[0];
const FAMILIARITY = [
  'I journal regularly',
  "I've tried, but it never stuck",
  'I want to, but lack the motivation',
  "I'm completely new to it",
];
const ISSUES = ['Work', 'School', 'Relationships', 'Family', 'Finances', 'General stress & anxiety'];

const TAILORED: Record<string, { title: string; body: string }> = {
  Work: { title: 'Work weighs heavy.', body: 'Regular reflection is shown to lower burnout and help you actually switch off at night.' },
  School: { title: 'School is a lot.', body: 'Writing your feelings and thoughts down clears the mental clutter — so you can focus, and remember more.' },
  Relationships: { title: 'Relationships are tender.', body: 'Naming what you feel helps you respond with intention, instead of reacting in the moment.' },
  Family: { title: 'Family stays with you.', body: 'Reflection creates a little space between you and the noise — and a lot more patience.' },
  Finances: { title: 'Money is stressful.', body: 'Getting the worry out of your head and onto the page quiets the late-night spiral.' },
  'General stress & anxiety': { title: "You're carrying a lot.", body: 'Journaling is one of the most studied ways to calm an anxious, racing mind.' },
};

const STEPS = [
  'welcome',
  'name', 'age', 'gender', 'occupation',
  'emotional', 'familiarity', 'issues',
  'tailored', 'benefit', 'stat', 'reread', 'reveal1', 'reveal2',
  'commit', 'notifications',
] as const;

// Keyword highlighting: wrap a phrase in ==double-equals== and it renders with a
// soft yellow marker behind it (mirrors the web app's `==…==` highlight).
const HL_BG = 'rgba(232,191,90,0.38)';
// Highlight word-by-word rather than wrapping the whole phrase in one <Text>.
// A single highlighted <Text> that wraps paints its background across the empty
// space to the line edge — so multi-word highlights left ugly yellow bars at
// line breaks. Backing only the words (spaces stay un-painted) keeps the marker
// snug to the text on every line.
function renderRich(text: string, color: string): ReactNode[] {
  return text.split(/(==[^=]+==)/g).flatMap((seg, i) => {
    if (!(seg.startsWith('==') && seg.endsWith('=='))) return [seg];
    return seg
      .slice(2, -2)
      .split(/(\s+)/)
      .map((part, j) =>
        part.trim() === '' ? (
          part
        ) : (
          <Text key={`${i}-${j}`} style={{ backgroundColor: HL_BG, color }}>
            {part}
          </Text>
        ),
      );
  });
}

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

function StoryView({ title, body, mood }: { title: string; body: string; mood?: MascotMood }) {
  return (
    <View style={{ flex: 1, justifyContent: 'center' }}>
      {mood ? <Mascot mood={mood} size={108} style={{ marginBottom: 20 }} /> : null}
      <Text style={{ fontFamily: fonts.serifMedium, fontSize: 30, lineHeight: 39, color: '#2A2825' }}>
        {renderRich(title, '#2A2825')}
      </Text>
      <Text style={{ fontFamily: fonts.serif, fontSize: 19, lineHeight: 30, color: '#6E6B64', marginTop: 16 }}>
        {renderRich(body, '#6E6B64')}
      </Text>
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
  const [answers, setAnswers] = useState<OnboardingAnswers>({ issues: [] });
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
    case 'welcome':
      content = (
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
          <Mascot mood="happy" size={168} style={{ marginBottom: 24 }} />
          <Text
            style={{ fontFamily: fonts.serifMedium, fontSize: 40, lineHeight: 46, color: '#2A2825', textAlign: 'center' }}
          >
            Welcome to JAI
          </Text>
          <Text
            style={{ fontFamily: fonts.serif, fontSize: 19, lineHeight: 30, color: '#6E6B64', marginTop: 16, textAlign: 'center' }}
          >
            Your warm companion for clearer, calmer days — let&apos;s set things up in a minute.
          </Text>
        </View>
      );
      footer = <PrimaryButton label="Get started" onPress={next} />;
      break;

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

    case 'emotional':
      content = (
        <>
          <Heading title="How's your emotional health lately?" subtitle="No wrong answer — just where you are." />
          <ChoiceGroup options={EMOTIONAL} value={answers.emotional} onChange={(v) => set('emotional', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.emotional} />;
      break;

    case 'familiarity':
      content = (
        <>
          <Heading title="How do you relate to journaling?" subtitle="Pick the one that fits best." />
          <ChoiceGroup options={FAMILIARITY} value={answers.familiarity} onChange={(v) => set('familiarity', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.familiarity} />;
      break;

    case 'issues': {
      // When the user reports feeling great, reframe the same multi-select from
      // "what's weighing on you" to "what could be even better".
      const positive = answers.emotional === SUPER_POSITIVE;
      content = (
        <>
          <Heading
            title={positive ? 'What could be even better?' : "What's weighing on you right now?"}
            subtitle="Pick all that apply."
          />
          <ChoiceGroup multi options={ISSUES} value={answers.issues ?? []} onChange={(v) => set('issues', v)} />
        </>
      );
      footer = <PrimaryButton label="Continue" onPress={next} disabled={!answers.issues?.length} />;
      break;
    }

    case 'benefit':
      content = (
        <StoryView
          mood="thinkExcited"
          title="Journaling works."
          body="It's scientifically shown to ease anxiety, bring emotional clarity, and even improve your sleep and focus."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'tailored':
      content = <StoryView mood="sad" title={tailored.title} body={tailored.body} />;
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'stat':
      content = (
        <StoryView
          mood="thinkSad"
          title="But here's the catch."
          body="Studies show that even with all these benefits, around ==70% of people== say they couldn't stick with journaling after a while — it's just hard to keep up alone."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'reread':
      content = (
        <StoryView
          mood="writing"
          title="Also…"
          body="Most of journaling's power comes from ==rereading== your past entries — and almost nobody ever does. It's the part that quietly gets skipped."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'reveal1':
      content = (
        <StoryView
          mood="happy"
          title="So we made it effortless."
          body="JAI turns journaling into a warm conversation. You just talk — it listens, remembers, and gently asks the right questions."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'reveal2':
      content = (
        <StoryView
          mood="thinkExcited"
          title="And it rereads for you."
          body="JAI quietly ==connects the dots== across your days, ==tracks your patterns==, and is ready with ==grounded advice== whenever you ask."
        />
      );
      footer = <PrimaryButton label="Continue" onPress={next} />;
      break;

    case 'commit':
      content = (
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
          <Text
            style={{ fontFamily: fonts.serifMedium, fontSize: 30, lineHeight: 39, color: '#2A2825', textAlign: 'center' }}
          >
            Do you want to transform your life?
          </Text>
          <Text
            style={{ fontFamily: fonts.serif, fontSize: 18, lineHeight: 28, color: '#6E6B64', marginTop: 16, textAlign: 'center' }}
          >
            It starts with a small, daily promise to yourself.
          </Text>
          <View style={{ marginTop: 52 }}>
            <HoldToCommit label="Hold to commit" onComplete={next} />
          </View>
        </View>
      );
      footer = null;
      break;

    case 'notifications':
      content = (
        <View style={{ flex: 1, justifyContent: 'center' }}>
          <Mascot mood="writing" size={108} style={{ marginBottom: 20 }} />
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
