export function passwordChecks(value = '') {
  const types = [/[A-Z]/.test(value), /[a-z]/.test(value), /[0-9]/.test(value), /[!-/:-@\[-`{-~]/.test(value)]
  return { length: value.length >= 12 && value.length <= 128, types, count: types.filter(Boolean).length }
}
export function validPassword(value) {
  const checks = passwordChecks(value)
  return checks.length && checks.count >= 3
}
