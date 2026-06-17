import AsyncStorage from '@react-native-async-storage/async-storage';

export type OnboardingAnswers = {
  name?: string;
  age?: string;
  gender?: string;
  occupation?: string;
  emotional?: string;
  familiarity?: string;
  issues?: string[];
};

const KEY = 'jai_onboarding_answers';

export async function saveAnswers(a: OnboardingAnswers): Promise<void> {
  try {
    await AsyncStorage.setItem(KEY, JSON.stringify(a));
  } catch {
    // best-effort
  }
}

export async function loadAnswers(): Promise<OnboardingAnswers> {
  try {
    const v = await AsyncStorage.getItem(KEY);
    return v ? (JSON.parse(v) as OnboardingAnswers) : {};
  } catch {
    return {};
  }
}

// Flat, PostHog-friendly shape (arrays → comma strings) for person properties.
export function flattenAnswers(a: OnboardingAnswers): Record<string, string> {
  return {
    name: a.name ?? '',
    age: a.age ?? '',
    gender: a.gender ?? '',
    occupation: a.occupation ?? '',
    emotional: a.emotional ?? '',
    familiarity: a.familiarity ?? '',
    issues: (a.issues ?? []).join(', '),
  };
}
