## Framework & Imports

Cypress JavaScript | no imports needed

## Commands

cy.get() / cy.contains() / cy.findByRole()

## Assertions

.should('be.visible') / .should('have.text', '...') / .should('eq', ...)

## Network

cy.intercept('POST', '/api/...', { fixture: 'data.json' })

## Structure

describe('Suite', () => { it('case', () => { ... }) })
