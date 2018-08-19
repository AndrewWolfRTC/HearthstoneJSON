#!/usr/bin/env python

import argparse
import collections
import logging
import os
import subprocess
import time
from datetime import datetime
from logging.handlers import QueueHandler
from urllib.parse import urlparse

import boto3
import requests
from influxdb import InfluxDBClient


DEVNULL = open(os.devnull, "w")

MESSAGE = """
@everyone
**Old:**
```
{old}
```

**New:**
```
{new}
```
""".strip()


class DequeAdapter(collections.deque):
	"""Adapts a deque to the queue interface expected by QueueHandler."""

	def put_nowait(self, obj):
		self.append(obj)


class AlarmOBot:
	def __init__(self, args):
		p = argparse.ArgumentParser(prog="alarmobot")
		p.add_argument("--bin", required=True)
		p.add_argument("--patch-dir", default=".")
		p.add_argument("--logfile")
		p.add_argument("--webhook-url", nargs="*")
		p.add_argument("--influx-url", nargs="?")
		p.add_argument("--simulate-new-build", action="store_true")
		p.add_argument("--from-email", nargs="?", default="root@localhost")
		p.add_argument("--to-email", nargs="*")
		p.add_argument("--post-url", nargs="*")
		self.args = p.parse_args(args)

		if not os.path.exists(self.args.patch_dir):
			os.makedirs(self.args.patch_dir)
		os.chdir(self.args.patch_dir)

		# Example url:
		# https://user:password@metrics.example.com:8086/dbname
		influx_url = self.args.influx_url
		if influx_url:
			url = urlparse(influx_url)
			self.influx = InfluxDBClient(
				host=url.hostname,
				port=url.port,
				username=url.username,
				password=url.password,
				database=url.path.lstrip("/"),
				ssl=url.scheme == "https",
				verify_ssl=url.scheme == "https",
				timeout=3,
			)
		else:
			self.influx = None

		self.ses = boto3.client("ses")
		self.simulate_new_build = self.args.simulate_new_build
		self.check_count = 0

		if self.args.logfile:
			logging.basicConfig(filename=self.args.logfile)
		else:
			logging.basicConfig()

		self.logger = logging.getLogger("alarmobot")
		self.logger.setLevel(logging.DEBUG)

		self.log_buffer = DequeAdapter([], 10)
		self.logger.addHandler(QueueHandler(self.log_buffer))

	def call_proc(self, args, log_stdout=False, log_stderr=False):
		log_args = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)

		if log_stdout and log_stderr:
			log_args["stderr"]=subprocess.STDOUT

		proc = subprocess.Popen(args, **log_args)

		while True:
			output = ""

			if log_stdout:
				output = proc.stdout.readline().decode()
			elif log_stderr:
				output = proc.stderr.readline().decode()

			if not output:
				if proc.poll() is not None:
					break
			else:
				self.logger.debug(output.strip())

		return proc

	def write_to_influx(self, buildinfo):
		if not self.influx:
			return
		self.logger.debug("Writing buildinfo %r to InfluxDB", buildinfo)
		result = self.influx.write_points([{
			"measurement": "hsb_build",
			"tags": {"build": buildinfo},
			"fields": {"count": 1},
			"time": datetime.now().isoformat(),
		}])
		if not result:
			self.logger.warning("Error writing points to InfluxDB")

	def write_to_discord(self, message, tts=False):
		if self.args.webhook_url:
			for webhook_url in self.args.webhook_url:
				self.logger.debug("Sending to Discord: %r", message)
				resp = requests.post(webhook_url, json={"content": message, "tts": tts})
				logging.debug("Response: %r", resp)

	def send_email(self, message):
		if not self.args.to_email:
			return

		self.logger.debug("Attempting to send email")
		try:
			self.ses.send_email(
				Source="Alarm-o-Bot <{}>".format(self.args.from_email),
				Destination={"ToAddresses": self.args.to_email},
				Message={
					"Subject": {
						"Charset": "UTF-8",
						"Data": "Hearthstone build data updated",
					},
					"Body": {
						"Text": {
							"Charset": "UTF-8",
							"Data": message.replace("\n", "\r\n"),
						}
					}
				}
			)
		except Exception:
			self.logger.exception("Exception while sending email")

	def get_buildinfo(self):
		proc = self.call_proc([self.args.bin, "--buildinfo"])
		stdout, stderr = proc.communicate()
		err = stderr.decode("utf-8").strip() if stderr else ""
		return stdout.decode("utf-8").strip(), err

	def compare_builds(self, old_build, new_build):
		self.check_count += 1

		if old_build != new_build:
			return True

		if self.simulate_new_build and self.check_count == 1:
			self.logger.info("Simulating a new build arriving")
			self.simulate_new_build = False
			return True

		return False

	def on_new_build(self, old, new):
		# Send an alert on Influx
		self.write_to_discord(
			"@everyone Hearthstone build data updated",
			tts=True
		)
		message = MESSAGE.format(old=old, new=new)
		self.write_to_discord(message + "Downloading...")

		# Send emails
		self.send_email(message)

		if self.args.post_url:
			for url in self.args.post_url:
				requests.post(url)

		# Start downloading the patch
		self.logger.info("Downloading to {}...".format(self.args.patch_dir))
		ngdptool_proc = self.call_proc(
			[self.args.bin],
			log_stdout=True,
			log_stderr=True
		)

		ngdptool_proc.wait()
		if ngdptool_proc.returncode == 0:
			self.write_to_discord(
				"Successfully downloaded new build to {}".format(
					self.args.patch_dir
				)
			)
		else:
			self.write_to_discord(
				"@everyone Patch download failed: ```{}```".format(
					"\n".join(map(lambda lr: lr.getMessage(), self.log_buffer))
				)
			)

	def run(self):
		try:
			buildinfo, err = self.get_buildinfo()
			if not buildinfo:
				self.logger.warning(err)
				raise RuntimeError("Could not get initial build info")
			self.logger.info("Current buildinfo: %s", buildinfo)
			while True:
				new_buildinfo, err = self.get_buildinfo()
				if not new_buildinfo:
					continue

				try:
					buildname = new_buildinfo.splitlines()[0].split()[1]
				except ValueError:
					buildname = "(invalid)"

				if self.compare_builds(buildinfo, new_buildinfo):
					self.logger.info("New buildinfo: %s", new_buildinfo)
					self.on_new_build(buildinfo, new_buildinfo)
					buildinfo = new_buildinfo

				self.write_to_influx(buildname)

				time.sleep(5)
		except KeyboardInterrupt:
			pass

		return 0


def main():
	import sys
	app = AlarmOBot(sys.argv[1:])
	exit(app.run())


if __name__ == "__main__":
	main()
