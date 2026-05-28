def main() -> None:
    from reporting.state_machine import ReportingStateMachine

    machine = ReportingStateMachine()
    machine.run()


if __name__ == "__main__":
    main()
