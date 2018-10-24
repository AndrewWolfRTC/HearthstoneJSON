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
from keg.remote.http import HttpRemote


DEVNULL = open(os.devnull, "w")

MESSAGE = """
{mention}
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
		p.add_argument("--ngdp-bin", required=True)
		p.add_argument("--ngdp-dir", required=True) # dir that contains .ngdp
		p.add_argument("--logfile")
		p.add_argument("--webhook-url", nargs="*")
		p.add_argument("--influx-url", nargs="?")
		p.add_argument("--simulate-new-build", action="store_true")
		p.add_argument("--from-email", nargs="?", default="root@localhost")
		p.add_argument("--to-email", nargs="*")
		p.add_argument("--post-url", nargs="*")
		self.args = p.parse_args(args)

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

		if self.args.to_email and self.args.from_email:
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

		self.mention = "" if self.simulate_new_build else "@everyone"

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

	def call_ngdp(self, args):
		ngdp_dir = os.path.join(self.args.ngdp_dir, ".ngdp")
		return self.call_proc(
			[self.args.ngdp_bin, "--ngdp-dir", ngdp_dir, "--no-progress", *args],
			log_stdout=True,
			log_stderr=True
		)

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
		if not self.args.to_email or not self.args.from_email:
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

	def compare_versions(self, old_version, new_version):
		if not old_version or not old_version.versions_name:
			raise ValueError("old_version is not a valid version object: %s" % old_version)
		if not new_version or not new_version.versions_name:
			raise ValueError("new_version is not a valid version object: %s" % new_version)

		self.check_count += 1

		if old_version.versions_name != new_version.versions_name:
			return True

		if self.simulate_new_build and self.check_count == 1:
			self.logger.info("Simulating a new build arriving")
			self.simulate_new_build = False
			return True

		return False

	def on_new_build(self, old, new):
		# Send an alert on Influx
		self.write_to_discord(
			f"{self.mention} Hearthstone build data updated",
			tts=True
		)
		message = MESSAGE.format(
			mention=self.mention, old=old.versions_name, new=new.versions_name
		)
		self.write_to_discord(message + "Downloading...")

		# Send emails
		self.send_email(message)

		if self.args.post_url:
			for url in self.args.post_url:
				requests.post(url)

		out_dir = os.path.join(self.args.ngdp_dir, new.build_id)

		# Start downloading the patch
		self.logger.info("Downloading...")

		ngdp_proc = self.call_ngdp(["fetch", "hsb"])

		ngdp_proc.wait()
		if ngdp_proc.returncode != 0:
			error = "\n".join(map(lambda lr: lr.getMessage(), self.log_buffer))
			self.write_to_discord(f"{self.mention} Patch download failed: ```{error}```")
			return

		self.write_to_discord(
			f"Successfully downloaded new build, installing to {out_dir}..."
		)

		ngdp_proc = self.call_ngdp([
			"install",
			"hsb",
			new.build_config,
			out_dir
		])

		ngdp_proc.wait()
		if ngdp_proc.returncode != 0:
			error = "\n".join(map(lambda lr: lr.getMessage(), self.log_buffer))
			self.write_to_discord(
				f"{self.mention} Patch installation failed: ```{error}```"
			)
		else:
			self.write_to_discord(
				"Successfully installed new build to {}".format(
					out_dir
				)
			)

	def get_latest_version(self):
		remote = HttpRemote("http://us.patch.battle.net:1119/hsb")
		try:
			versions = remote.get_versions()
		except Exception:
			return None
		versions = [v for v in versions if v.region == "us"]
		return max(versions, key=lambda x: x.build_id)


	def check_for_new_version(self, current_version):
		new_version = self.get_latest_version()
		if not new_version:
			return

		if self.compare_versions(current_version, new_version):
			self.logger.info("New build: %s", new_version.versions_name)
			self.on_new_build(current_version, new_version)
			current_version = new_version

		self.write_to_influx(current_version.versions_name)
		return current_version


	def run(self):
		try:
			version = self.get_latest_version()
			self.logger.info("Current build: %s", version.versions_name)
			while True:
				version = self.check_for_new_version(version)
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
